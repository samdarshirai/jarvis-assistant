import asyncio
import contextlib
import json
import logging
import re

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from starlette.websockets import WebSocket

from jarvis.agent.graph import turn_replies
from jarvis.llm import MemoryContextError
from jarvis.tenant import UnknownUser, thread_config
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, HANDLED_TEXT, PENDING_TEXT
from jarvis.voice import protocol as P
from jarvis.voice.confirm import match_confirmation
from jarvis.voice.stt import DeepgramSTT
from jarvis.voice.tts import DeepgramTTS, split_sentences

log = logging.getLogger(__name__)

TAP_ONLY = {"send_draft"}  # irreversible and third-party-facing: a spoken yes is never enough
TAP_TEXT = "I need you to tap Confirm on the screen to send it."
STT_DOWN_TEXT = "Sorry, I can't hear you right now."
WARN_TEXT = "Heads up, I came up with this after reading an email or a web page, so double-check it. "
ACTION_RESULTS = {  # each names "the call" so the keyword router sends the turn to the phone domain
    "ambiguous": "",
    "not_found": "the call to {} failed: no contact with that name was found. Tell the user.",
    "no_permission": "the call to {} was not made: the app has no permission to read contacts. Tell the user.",
    "error": "the call to {} failed with an error. Tell the user.",
}


def _clean(name: str) -> str:
    """Names come from the phone's contacts: only letters, digits, spaces and .'- reach the model."""
    kept = "".join(c for c in name if c.isalnum() or c in ".'-" or c.isspace())  # split() below turns any whitespace into one space
    return " ".join(kept.split())[:P.MAX_NAME_CHARS]


def card_lines(payload: dict) -> list[str]:
    return [a.get("summary") or f"{a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}" for a in payload["actions"]]


def is_tap_only(payload: dict) -> bool:
    return any(a["tool"] in TAP_ONLY for a in payload["actions"])


class VoiceSession:
    def __init__(self, ws: WebSocket, graph, devices, device_id: int, stt, tts, lock: asyncio.Lock, steps: set,
                 cfg: dict, *, user_id: int, token: str):
        self.ws, self.graph, self.devices, self.device_id, self.stt, self.tts = ws, graph, devices, device_id, stt, tts
        self.user_id, self.token = user_id, token  # the token is re-verified before every graph step
        self.lock, self.steps = lock, steps
        self.cfg = cfg  # the user's one thread (FR-1), voice flag set: selects the fast tier
        self.state = "listening"
        self.turn: asyncio.Task | None = None
        self.utterance_bytes = 0
        self.stt_failed = False  # the failure path can be reached from the input loop and the events task; run it once
        self.offered: str | None = None  # id of the last card this session presented; only it may be answered aloud

    # --- output ---
    async def send(self, type_: str, **fields) -> bool:
        try:
            await self.ws.send_text(P.frame(type_, **fields))
            return True
        except Exception:  # socket already gone
            return False

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if state == "listening":
                self.utterance_bytes = 0
            await self.send("state", state=state)

    async def close(self, code: int) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)

    async def _say(self, texts: list[str]) -> None:
        sentences = [s for t in texts for s in split_sentences(t)]
        for t in texts:
            await self.send("transcript", role="assistant", text=t, final=True)
        if sentences:
            await self.set_state("speaking")
            try:
                async for chunk in self.tts.synth(sentences):
                    await self.ws.send_bytes(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error("tts failed")
                await self.send("error", message="Speech output failed; the reply is in the transcript.")
        await self.set_state("listening")

    # --- turn control ---
    async def _stop_turn(self) -> None:
        t, self.turn = self.turn, None
        if t and not t.done():
            t.cancel()
            await asyncio.wait({t})  # inner outcome is not raised; our own cancellation still propagates

    async def _start(self, coro) -> None:
        await self._stop_turn()
        self.turn = asyncio.create_task(self._guarded(coro))

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except MemoryContextError as error:
            await self._failed(str(error))
        except Exception:
            await self._failed()

    async def _failed(self, text: str = FAIL_TEXT) -> None:
        log.error("voice turn failed")
        await self.send("error", message=text)
        await self._say([text])
        with contextlib.suppress(Exception):  # buttons may be gone: re-offer a still-pending confirmation
            state = await self.graph.aget_state(self.cfg)
            if state.interrupts:
                await self._card(state.interrupts[0])

    # --- graph steps ---
    async def _step(self, decide):
        """Run `decide` (read pending -> decide -> graph.ainvoke) as one step under the lock shared with Telegram,
        and return the follow-up it chose (speech, card) to run after the lock is released.

        Waiting for the lock or authorization can be cancelled: nothing has happened yet. Once authorized the
        step is its own task, shielded from the turn: barge-in, a new final, `cancel`, disconnect or replacement only abandon
        waiting for it. The step always finishes, audits and checkpoints, and holds the lock until then.

        A revoked device or a user disabled mid-session (by the CLI, another process: verify reads the DB) runs no
        step; the session is closed instead."""
        await self.lock.acquire()
        try:
            device = await asyncio.to_thread(self.devices.verify, self.token)
        except Exception:
            self.lock.release()
            log.error("device verification failed")
            return self._unauthorized
        except BaseException:  # cancellation while verifying starts no graph work
            self.lock.release()
            raise
        if device is None:
            self.lock.release()
            return self._unauthorized

        async def run():
            try:
                return await decide()
            finally:
                self.lock.release()

        t = asyncio.create_task(run())
        self.steps.add(t)  # a strong reference, so an abandoned step is not garbage collected
        t.add_done_callback(self._step_done)
        # wait() does not cancel the step when this turn is cancelled. Unlike shield() on Python 3.14,
        # it also leaves abandoned error logging to _step_done instead of logging the raw exception.
        await asyncio.wait({t})
        return t.result()

    async def _unauthorized(self) -> None:
        await self.send("error", message="This device is no longer signed in.")
        await self.close(P.CLOSE_UNAUTHORIZED)

    def _step_done(self, t: asyncio.Task) -> None:
        self.steps.discard(t)
        if not t.cancelled() and t.exception() is not None:  # also marks it retrieved when nobody waits any more
            log.error("graph step failed")

    async def _utterance(self, text: str) -> None:
        await self.set_state("thinking")

        async def decide():
            pending = (await self.graph.aget_state(self.cfg)).interrupts
            if not pending:
                result = await self.graph.ainvoke({"messages": [HumanMessage(text)]}, self.cfg)
                return lambda: self._present(result)
            it = pending[0]
            if it.id != self.offered:  # never presented here (Telegram, an earlier session): present, never resume
                return lambda: self._offer(it)
            decision = match_confirmation(text)
            if decision is None or (decision and is_tap_only(it.value)):
                return lambda: self._refuse(it, TAP_TEXT if decision else PENDING_TEXT)
            result = await self.graph.ainvoke(Command(resume=decision), self.cfg)
            return lambda: self._present(result)

        await (await self._step(decide))()

    async def _refuse(self, it, text: str) -> None:
        await self._card(it)  # re-show the card; nothing was decided
        await self._say([text])

    async def _speak_text(self, m: dict) -> None:
        text = m.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > P.MAX_SPEAK_CHARS:
            await self.send("error", message="speak needs text of 1 to 2000 characters.")
            return
        await self._say([text])

    @staticmethod
    def _action_text(m: dict) -> str | None:
        """The utterance for a valid action_result frame, else None. Sync, so a bad frame never touches the running turn."""
        contact, matches, status = m.get("contact"), m.get("matches", []), m.get("status")
        ok = (m.get("action") == "call_contact" and status in ACTION_RESULTS
              and isinstance(contact, str) and len(contact) <= P.MAX_CONTACT_CHARS and _clean(contact)
              and isinstance(matches, list) and len(matches) <= P.MAX_MATCHES
              and (status != "ambiguous" or len(matches) >= 2)
              and all(isinstance(x, dict) and isinstance(x.get("name"), str) and isinstance(x.get("last4"), str)
                      and re.fullmatch(r"[0-9]{4}", x["last4"]) for x in matches))
        if not ok:
            return None
        who = f'"{_clean(contact)}"'  # _clean always strips ", so a name cannot close its own quotes
        if status == "ambiguous":
            found = ", ".join(f'"{_clean(x["name"])}" ending {x["last4"]}' for x in matches)
            return f"Phone result: the call to {who} matches {len(matches)} contacts: {found}. Ask which one."
        return f"Phone result: {ACTION_RESULTS[status].format(who)}"

    async def _tap(self, m: dict) -> None:
        await self.set_state("thinking")
        decision = m.get("decision")

        async def decide():
            interrupts = (await self.graph.aget_state(self.cfg)).interrupts
            if decision not in ("yes", "no") or not interrupts or interrupts[0].id != m.get("interrupt_id"):
                return lambda: self._say([HANDLED_TEXT])  # stale, repeated or bare taps never resume anything
            result = await self.graph.ainvoke(Command(resume=decision == "yes"), self.cfg)
            return lambda: self._present(result)

        await (await self._step(decide))()

    async def _present(self, result: dict) -> None:
        interrupts = result.get("__interrupt__")
        if interrupts:
            await self._offer(interrupts[0])
            return
        if result.get("client_actions"):  # before the speech, so an alarm is set while Jarvis is still talking
            await self.send("client_actions", actions=result["client_actions"])
            if any(a.get("type") == "call_contact" for a in result["client_actions"]):
                await self._say([])  # the call is the answer; the phone reports back only when it cannot place it
                return
        await self._say(turn_replies(result["messages"]) or [EMPTY_TEXT])

    # --- confirmation cards ---
    async def _card(self, it) -> None:
        payload = it.value
        self.offered = it.id
        await self.send("confirm_card", interrupt_id=it.id, summary="\n".join(card_lines(payload)),
                        tap_only=is_tap_only(payload), after_untrusted=bool(payload.get("after_untrusted")))

    async def _offer(self, it) -> None:
        await self._card(it)
        payload = it.value
        spoken = (WARN_TEXT if payload.get("after_untrusted") else "") + "; ".join(card_lines(payload))
        await self._say([spoken + (". " + TAP_TEXT if is_tap_only(payload) else ". Want me to go ahead? Just say yes or no.")])

    # --- input ---
    async def _consume_events(self) -> None:
        try:
            async for ev in self.stt.events():
                if ev.kind == "partial":
                    await self.send("transcript", role="user", text=ev.text, final=False)  # first: the app flushes on it
                    if self.state == "speaking":  # speech over playback = barge-in
                        await self._stop_turn()
                        await self.set_state("listening")
                else:
                    self.utterance_bytes = 0
                    await self.send("transcript", role="user", text=ev.text, final=True)
                    await self._start(self._utterance(ev.text))
            # a clean end of the stream is a failure too (cancellation raises instead, so teardown never lands here)
            log.error("stt stream ended unexpectedly")
            await self._stt_failed()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("stt failed")
            await self._stt_failed()

    async def _stt_failed(self) -> None:
        if self.stt_failed:
            return
        self.stt_failed = True
        await self.send("error", message="Speech recognition is unavailable.")
        with contextlib.suppress(Exception):
            await self._say([STT_DOWN_TEXT])
        await self.close(P.CLOSE_UPSTREAM)

    async def _audio(self, data: bytes) -> bool:
        if len(data) > P.MAX_AUDIO_FRAME:
            await self.send("error", message="Audio frame too large.")
            await self.close(P.CLOSE_TOO_BIG)
            return False
        if self.state == "listening":  # the cap bounds how long the mic can stay open, not reply playback
            self.utterance_bytes += len(data)
            if self.utterance_bytes > P.MAX_UTTERANCE_BYTES:
                await self.send("error", message="Utterance too long.")
                await self.close(P.CLOSE_TOO_LONG)
                return False
        try:
            await self.stt.send(data)
        except Exception:  # the upstream STT socket died: same path as a failed event stream
            log.error("stt send failed")
            await self._stt_failed()
            return False
        return True

    async def _control(self, text: str) -> bool:
        m = P.parse(text)
        if m is None:
            await self.send("error", message="Bad frame.")
            return True
        kind = m["type"]
        if kind == "hello":
            fcm = m.get("fcm_token")
            if isinstance(fcm, str) and fcm:
                await asyncio.to_thread(self.devices.set_fcm, self.device_id, fcm)
        elif kind == "confirm":
            await self._start(self._tap(m))
        elif kind == "cancel":
            await self._stop_turn()
            await self.set_state("listening")
        elif kind == "speak":
            await self._start(self._speak_text(m))
        elif kind == "action_result":
            text = self._action_text(m)
            if text is None:
                await self.send("error", message="Bad action result.")
            else:
                await self._start(self._utterance(text))
        elif kind == "bye":
            return False
        else:
            await self.send("error", message=f"Unknown frame type: {kind}")
        return True

    async def run(self) -> None:
        events = asyncio.create_task(self._consume_events())
        try:
            await self.send("state", state="listening")
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not await self._audio(msg["bytes"]):
                        break
                elif msg.get("text") is not None:
                    if not await self._control(msg["text"]):
                        break
        except RuntimeError:  # receive() after the peer closed
            pass
        finally:
            events.cancel()
            try:  # nested so a cancellation of run() itself still releases the turn and the stt stream
                await asyncio.gather(events, return_exceptions=True)  # no new turn can start after this
            finally:
                try:
                    await self._stop_turn()
                finally:
                    with contextlib.suppress(Exception):
                        await self.stt.close()


class VoiceService:
    hello_timeout = 10.0  # a refused client that never says hello is closed after this, not held open

    def __init__(self, tenants, devices, stt_factory=DeepgramSTT, tts_factory=DeepgramTTS):
        self.tenants, self.devices = tenants, devices
        self.stt_factory, self.tts_factory = stt_factory, tts_factory  # (deepgram_key) -> STT / TTS
        self.steps: set[asyncio.Task] = set()  # in-flight graph steps, including ones a cancelled turn abandoned
        self.sessions: dict[int, VoiceSession] = {}  # device id -> its one live session

    async def handle(self, ws: WebSocket) -> None:
        auth = ws.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        device = await asyncio.to_thread(self.devices.verify, token) if token else None  # None for a disabled user
        try:
            rt = await self.tenants.get(device.user_id) if device else None
        except UnknownUser:
            rt = None
        if rt is None:
            await ws.close(code=P.CLOSE_UNAUTHORIZED)  # before accept: no audio is ever read from a stranger
            return
        key = self.tenants.s.deepgram_api_key  # the operator's, shared by all users
        missing = P.NEEDS_DEEPGRAM if not key else P.NEEDS_LLM if rt.graph is None else None
        await ws.accept()
        if missing:
            await self._take_hello(ws, device.device_id)  # push must work before the user adds a Deepgram key
            await ws.send_text(P.frame("error", code=missing))
            await ws.close(code=P.CLOSE_NEEDS_SETUP)
            return
        uid = device.user_id
        try:
            stream = await self.stt_factory(key).open()
        except Exception:
            log.error("could not open stt")
            await ws.send_text(P.frame("error", message="Speech recognition is unavailable."))
            await ws.close(code=P.CLOSE_UPSTREAM)
            return
        session = VoiceSession(ws, rt.graph, self.devices, device.device_id, stream, self.tts_factory(key),
                               self.tenants.lock(uid), self.steps, thread_config(uid, voice=True),
                               user_id=uid, token=token)
        old = self.sessions.pop(device.device_id, None)
        self.sessions[device.device_id] = session
        if old:
            await old.close(P.CLOSE_REPLACED)
        try:
            await session.run()
        finally:
            if self.sessions.get(device.device_id) is session:
                del self.sessions[device.device_id]
            with contextlib.suppress(Exception):
                await ws.close()

    async def _take_hello(self, ws: WebSocket, device_id: int) -> None:
        """Store the fcm token of the client's first frame when it is a hello; best effort, bounded wait."""
        try:
            msg = await asyncio.wait_for(ws.receive(), self.hello_timeout)
        except (asyncio.TimeoutError, RuntimeError):
            return
        m = P.parse(msg.get("text") or "")  # parse() enforces the text frame cap; bytes or a disconnect parse to None
        fcm = m.get("fcm_token") if m and m["type"] == "hello" else None
        if isinstance(fcm, str) and fcm:
            await asyncio.to_thread(self.devices.set_fcm, device_id, fcm)

    async def deliver(self, user_id: int, actions: list[dict]) -> bool:
        """Push to that user's most recently connected session; False when they have none (or it is gone)."""
        for s in reversed([s for s in self.sessions.values() if s.user_id == user_id]):
            if await s.send("client_actions", actions=actions):
                return True
        return False
