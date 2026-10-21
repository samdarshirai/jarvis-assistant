import asyncio
import html
import logging
from contextlib import asynccontextmanager
from functools import partial

from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, StringConstraints
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from jarvis.appapi import AppService
from jarvis.audit import purge_all as purge_audit
from jarvis.channels.telegram import TelegramChannel
from jarvis.config import get_settings
from jarvis.dashboard import DashboardService
from jarvis.db import init_schema, make_pool
from jarvis.keys import Keys
from jarvis.proactive.mailwatch import undo_event
from jarvis.proactive.scheduler import start_proactive
from jarvis.proactive.store import purge_all as purge_proactive
from jarvis.signin import BadState, Busy, Refused, SignIn
from jarvis.tenant import Tenants
from jarvis.timeutil import now_local
from jarvis.users import UserStore, ensure_owner
from jarvis.voice.devices import Devices
from jarvis.voice.ws import VoiceService

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        owner_id = ensure_owner(pool, s)
        if owner_id is None:
            raise RuntimeError("No owner yet: set JARVIS_OWNER_EMAIL to the owner's Google email.")
        purge_audit(pool, 90)
        purge_proactive(pool, 90)
        devices = Devices(pool)

        async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
            await saver.setup()
            tenants = Tenants(pool, s, saver)
            try:
                # No LLM key yet is fine: owner.graph is None and chat fails until the owner sets one.
                owner = await tenants.get(owner_id)

                async def undo(event_id: str) -> bool:
                    rt = await tenants.get(owner_id)  # current runtime: the owner may have reconnected Google
                    user = await asyncio.to_thread(UserStore(pool).get, owner_id)
                    if user is None or user.disabled:
                        return False
                    return await undo_event(rt.calendar, rt.proactive, rt.audit, event_id)

                # Each user's voice session runs on their runtime and their Deepgram key; tenants.lock(uid) is shared by
                # every channel on that user's thread: "read pending -> decide -> ainvoke" is one step.
                voice = VoiceService(tenants, devices)
                app.state.voice = voice
                app.state.tenants, app.state.owner_id = tenants, owner_id
                if s.google_web_client_secrets and s.public_base_url:
                    loop = asyncio.get_running_loop()  # finish() runs in a worker thread; Tenants belongs to the loop
                    app.state.signin = SignIn(
                        pool, UserStore(pool), devices, s.fernet_key, client_secrets=s.google_web_client_secrets,
                        redirect_uri=s.public_base_url.rstrip("/") + "/auth/callback",
                        invalidate=lambda uid: loop.call_soon_threadsafe(tenants.invalidate, uid))
                # The app and dashboard look the caller's runtime up per request (auth_runtime), not the owner's.
                app.state.dashboard = DashboardService(devices, tenants, weather_city=s.weather_city)
                app.state.app_api = app_api = AppService(devices, tenants)
                app.state.keys = Keys(devices, tenants)
                tg = TelegramChannel(tenants, owner_id, s.telegram_owner_chat_id,
                                     deliver_actions=partial(voice.deliver, owner_id), undo=undo).build(s.telegram_bot_token)
                try:
                    await tg.initialize()
                except Exception:
                    # If initialize failed, still try to shut down
                    try:
                        await tg.shutdown()
                    except Exception:
                        log.exception("failed to shutdown telegram app after init failure")
                    raise
                sched = None
                try:
                    await tg.start()
                    await tg.updater.start_polling()
                    sched = start_proactive(s, tenants, UserStore(pool), tg.bot, devices)
                    try:
                        yield
                    finally:
                        pass  # Teardown happens in outer finally
                finally:
                    # Ordered teardown for all paths through start/start_polling/yield
                    if sched is not None:
                        sched.shutdown(wait=False)  # stop new jobs before the channels and pool go away
                    app.state.voice = None  # a connection arriving during teardown is refused (1013) instead of reaching a closing service
                    app.state.dashboard = None
                    app.state.app_api = None
                    app.state.keys = None
                    app.state.signin = None
                    app.state.tenants = None
                    try:
                        if tg.updater.running:
                            await tg.updater.stop()
                    except Exception:
                        log.exception("failed to stop updater")
                    try:
                        if tg.running:
                            await tg.stop()
                    except Exception:
                        log.exception("failed to stop telegram app")
                    try:
                        await tg.shutdown()
                    except Exception:
                        log.exception("failed to shutdown telegram app")
                    # A graph step an abandoned voice turn left running must finish (audit row + checkpoint) before the
                    # saver and pool close. asyncio.wait does not cancel on timeout, unlike wait_for(gather(...)).
                    try:
                        pending = set(voice.steps) | set(app_api.steps)
                        if pending:
                            await asyncio.wait(pending, timeout=30)
                    except Exception:
                        log.exception("voice steps did not drain")
            finally:
                await tenants.close()  # after the steps drained: closes each runtime's LLM http client
    finally:
        pool.close()


class TaskIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    due: date | None = None


class ChatIn(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class ConfirmIn(BaseModel):
    interrupt_id: str
    decision: Literal["yes", "no"]


Secret = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]  # pasted keys carry newlines
Models = Annotated[str, StringConstraints(max_length=500)]


class LLMIn(BaseModel):
    base_url: Annotated[str, StringConstraints(max_length=500)]
    api_key: Secret
    model_fast: Models | None = None  # omitted keeps the stored ids, "" clears them
    model_strong: Models | None = None


class KeyIn(BaseModel):
    api_key: Secret


class FcmIn(BaseModel):
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class MeIn(BaseModel):
    tz: Annotated[str, StringConstraints(max_length=100)] | None = None
    display_name: str | None = None


class StartIn(BaseModel):
    challenge: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]  # base64url(sha256), no padding


class ClaimIn(BaseModel):
    nonce: Annotated[str, StringConstraints(max_length=100)]
    verifier: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._~-]{43,128}$")]  # RFC 7636 code verifier
    code: Annotated[str, StringConstraints(max_length=100)]


def _page(message: str, status: int, link: str = "") -> HTMLResponse:
    """A fixed message; with a link, the page sends the browser back to the app."""
    go = ""
    if link:
        url = html.escape(link)
        go = f'<meta http-equiv="refresh" content="0;url={url}">'
        message += f' <a href="{url}">Open Jarvis</a>'
    return HTMLResponse(
        f'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">{go}'
        f'<body style="font-family:sans-serif;padding:2rem;text-align:center"><h2>Jarvis</h2><p>{message}</p>',
        status_code=status)


_CONTACT = "rai.samdarshi01@gmail.com"
_HOME = f"""<h1>Jarvis</h1>
<p>Jarvis is a private AI assistant for a small invited group. It works with your Google Calendar, Gmail and Tasks, and runs in an Android app.</p>
<p>Access is by invitation only. Contact: <a href="mailto:{_CONTACT}">{_CONTACT}</a></p>
<p><a href="/privacy">Privacy policy</a></p>"""
_PRIVACY = f"""<h1>Jarvis privacy policy</h1>
<p>Jarvis is a private assistant for invited users. It accesses your Google data only to act on your requests.</p>
<h2>What we access</h2>
<ul><li>Your Google email address, to sign you in.</li>
<li>Google Calendar, Tasks and Gmail (read and draft), only to answer or act on what you ask in the app.</li></ul>
<h2>What we store</h2>
<ul><li>Your Google access token and any API keys you add (LLM, Deepgram, Tavily), encrypted at rest.</li>
<li>Your notes, memories, chat history and an activity log, kept per user and never shown to other users.</li></ul>
<h2>Who sees it</h2>
<p>Your data is sent only to the services you configure: Google, and the LLM, speech and search providers whose API keys you supply. We do not sell your data or use it for advertising.</p>
<p>Jarvis's use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>, including the Limited Use requirements. Google user data is not used to develop or train generalized AI models.</p>
<h2>Deletion</h2>
<p>Email <a href="mailto:{_CONTACT}">{_CONTACT}</a> and your account and all stored data are deleted. You can also revoke access at <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>"""


def _static(body: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem">{body}')


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def home():
        return _static(_HOME)

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return _static(_PRIVACY)

    @app.get("/dashboard")
    async def dashboard(request: Request):
        svc = getattr(app.state, "dashboard", None)
        if svc is None:  # lifespan has not built it yet
            return JSONResponse({"error": "unavailable"}, status_code=503)
        rt = await svc.authenticate(request.headers.get("authorization", ""))
        if rt is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            lat, lon = float(request.query_params["lat"]), float(request.query_params["lon"])
            coords = (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
        except (KeyError, ValueError):
            coords = None
        return await svc.payload(rt, coords=coords)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "bad_request"}, status_code=422)

    async def guarded(request: Request, name: str = "app_api"):
        """(service app.state.<name>, caller's runtime, error response): 503 until the lifespan built it, 401 on a bad
        device token or a disabled user."""
        svc = getattr(app.state, name, None)
        if svc is None:
            return None, None, JSONResponse({"error": "unavailable"}, status_code=503)
        rt = await svc.authenticate(request.headers.get("authorization", ""))
        if rt is None:
            return None, None, JSONResponse({"error": "unauthorized"}, status_code=401)
        return svc, rt, None

    def llm_or_409(rt):
        return None if rt.llm_ready else JSONResponse({"error": "needs_setup", "missing": "llm"}, status_code=409)

    def found(body):
        return JSONResponse({"error": "not_found"}, status_code=404) if body is None else body

    def signin_or_503():
        svc = getattr(app.state, "signin", None)
        return svc, (JSONResponse({"error": "unavailable"}, status_code=503) if svc is None else None)

    @app.post("/auth/start")
    async def auth_start(body: StartIn):
        svc, err = signin_or_503()
        if err:
            return err
        try:
            nonce, url = await asyncio.to_thread(svc.start, body.challenge)
        except Busy:
            return JSONResponse({"error": "busy"}, status_code=503)
        return {"nonce": nonce, "url": url}

    @app.get("/auth/callback", response_class=HTMLResponse)
    async def auth_callback(code: str = "", state: str = "", error: str = ""):
        svc = getattr(app.state, "signin", None)
        if svc is None:
            return _page("Jarvis is not ready for sign-in yet.", 503)
        if error or not code:
            return _page("Sign-in was cancelled or the link is invalid. Open Jarvis and try again.", 400)
        try:
            link = await asyncio.to_thread(svc.finish, state, code)
        except BadState:
            return _page("That sign-in link is invalid or expired. Open Jarvis and try again.", 400)
        except Refused:
            return _page("That Google account is not on the list. Ask the owner to add your email.", 403)
        except Exception as e:
            log.error("google sign-in failed: %s", type(e).__name__)  # the type only: Google's text may carry the code
            return _page("Google did not accept the sign-in. Open Jarvis and try again.", 502)
        return _page("Signed in. Returning to Jarvis…", 200, link)

    @app.post("/auth/claim")
    async def auth_claim(body: ClaimIn):
        svc, err = signin_or_503()
        if err:
            return err
        token = await asyncio.to_thread(svc.claim, body.nonce, body.verifier, body.code)
        if token is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"token": token, "needs_setup": await asyncio.to_thread(svc.needs_setup, token)}

    @app.get("/calendar")
    async def calendar(request: Request, days: int = 7):
        svc, rt, err = await guarded(request)
        if err:
            return err
        try:
            start = date.fromisoformat(request.query_params["from"]) if "from" in request.query_params else None
        except ValueError:
            return JSONResponse({"error": "bad_request"}, status_code=422)
        return await svc.calendar_events(rt, start or now_local(rt.tz).date(), days)

    @app.get("/tasks")
    async def tasks(request: Request):
        svc, rt, err = await guarded(request)
        return err or await svc.task_list(rt)

    @app.post("/tasks")
    async def add_task(request: Request, body: TaskIn):
        svc, rt, err = await guarded(request)
        if err:
            return err
        task = await svc.create_task(rt, body.title, body.due)
        return JSONResponse({"error": "unavailable"}, status_code=502) if task is None else {"task": task}

    @app.post("/tasks/{task_id}/complete")
    async def complete_task(request: Request, task_id: str):
        svc, rt, err = await guarded(request)
        if err:
            return err
        if not await svc.complete_task(rt, task_id):
            return JSONResponse({"error": "unavailable"}, status_code=502)
        return {"ok": True}

    @app.get("/mail")
    async def mail(request: Request, limit: int = 20):
        svc, rt, err = await guarded(request)
        return err or await svc.mail_list(rt, limit)

    @app.get("/mail/{message_id}")
    async def mail_item(request: Request, message_id: str):
        svc, rt, err = await guarded(request)
        return err or found(await svc.mail_item(rt, message_id))

    @app.get("/notes")
    async def notes(request: Request):
        svc, rt, err = await guarded(request)
        return err or await svc.note_list(rt)

    @app.get("/notes/{note_id}")
    async def note(request: Request, note_id: int):
        svc, rt, err = await guarded(request)
        return err or found(await svc.note_item(rt, note_id))

    @app.post("/chat")
    async def chat(request: Request, body: ChatIn):
        svc, rt, err = await guarded(request)
        return err or llm_or_409(rt) or await svc.chat(rt, body.text)

    @app.post("/chat/confirm")
    async def chat_confirm(request: Request, body: ConfirmIn):
        svc, rt, err = await guarded(request)
        return err or llm_or_409(rt) or await svc.confirm(rt, body.interrupt_id, body.decision)

    @app.get("/me")
    async def me(request: Request):
        svc, rt, err = await guarded(request, "keys")
        return err or await svc.me(rt)

    @app.put("/me")
    async def put_me(request: Request, body: MeIn):
        svc, rt, err = await guarded(request, "keys")
        return err or await svc.update_me(rt, body.model_dump(exclude_unset=True))

    @app.put("/settings/llm")
    async def put_llm(request: Request, body: LLMIn):
        svc, rt, err = await guarded(request, "keys")
        return err or await svc.set_llm(rt, body.base_url, body.api_key, body.model_fast, body.model_strong)

    @app.put("/settings/{which}")
    async def put_key(request: Request, which: Literal["deepgram", "tavily"], body: KeyIn):
        svc, rt, err = await guarded(request, "keys")
        return err or await svc.set_key(rt, which, body.api_key)

    @app.delete("/settings/{which}")
    async def delete_key(request: Request, which: Literal["deepgram", "tavily"]):
        svc, rt, err = await guarded(request, "keys")
        return err or await svc.delete_key(rt, which)

    @app.put("/device/fcm")
    async def put_fcm(request: Request, body: FcmIn):
        """The calling device's push token. Over REST because a user without Deepgram never opens /voice."""
        svc = getattr(app.state, "keys", None)
        if svc is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        device = await asyncio.to_thread(svc.devices.verify, token) if token else None  # None for a disabled user
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        await asyncio.to_thread(svc.devices.set_fcm, device.device_id, body.token)
        return {"ok": True}

    @app.websocket("/voice")
    async def voice(ws: WebSocket):
        svc = getattr(app.state, "voice", None)
        if svc is None:  # lifespan has not built it (or voice is disabled)
            await ws.close(code=1013)
            return
        await svc.handle(ws)

    return app


app = create_app()
