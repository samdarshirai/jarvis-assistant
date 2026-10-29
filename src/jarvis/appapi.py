import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from jarvis.agent.graph import turn_replies
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, HANDLED_TEXT, PENDING_TEXT
from jarvis.google.auth import ReauthRequired
from jarvis.kg import views
from jarvis.llm import MemoryContextError
from jarvis.tenant import auth_runtime, thread_config
from jarvis.timeutil import now_local
from jarvis.voice.ws import card_lines, is_tap_only

log = logging.getLogger(__name__)
UNREAD_QUERY = "is:unread in:inbox newer_than:7d"
MAX_MAIL = 20


class AppService:
    """Device-token REST endpoints behind the app screens. Every method runs on the caller's runtime (`rt`) from
    authenticate(): its clients, graph, thread, lock and audit. Reads fail per source (None); chat mirrors the Telegram gate."""

    def __init__(self, devices, tenants, source_timeout: float = 12.0):
        self.devices, self.tenants, self.source_timeout = devices, tenants, source_timeout
        self.steps: set[asyncio.Task] = set()  # every user's chat graph steps; drained in lifespan teardown

    async def authenticate(self, authorization: str):
        """The caller's UserRuntime, or None (bad token, or a disabled or unknown user)."""
        return await auth_runtime(self.devices, self.tenants, authorization)

    async def _call(self, fn, *args):
        """(value, reauth); value is None when the source failed."""
        try:
            # ponytail: a timed-out call keeps its worker thread until Google returns; shared default executor across users
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout), False
        except ReauthRequired:
            return None, True
        except Exception:
            log.exception("app source failed")
            return None, False

    @staticmethod
    def _today(rt) -> str:
        return now_local(rt.tz).date().isoformat()

    def _task(self, t: dict, today: str) -> dict:
        due = t.get("due")
        return {"id": t["id"], "title": t.get("title") or "(no title)", "due": due, "overdue": bool(due and due < today)}

    # --- calendar / tasks ---
    async def calendar_events(self, rt, start: date, days: int) -> dict:
        days = max(1, min(days, 42))  # a six-week month grid
        a = datetime.combine(start, time.min, tzinfo=ZoneInfo(rt.tz))
        events, reauth = await self._call(rt.calendar.list_events, a, a + timedelta(days=days))
        return {"events": None if events is None else [
            {"id": e["id"], "summary": e.get("summary") or "(no title)", "start": e["start"], "end": e["end"],
             "location": e.get("location"), "all_day": len(e["start"] or "") == 10} for e in events], "reauth": reauth}

    async def create_event(self, rt, summary: str, start: datetime, end: datetime) -> dict | None:
        ev, _ = await self._call(rt.calendar.create_event, summary, start, end)
        if ev is None:
            return None
        await self._audit(rt, "create_event", {"summary": summary, "start": start.isoformat()}, {"id": ev["id"]})
        return {"id": ev["id"]}

    async def delete_event(self, rt, event_id: str) -> bool:
        r, _ = await self._call(rt.calendar.delete_event, event_id, "this")
        if r is None:
            return False
        await self._audit(rt, "delete_event", {"id": event_id}, r)
        return True

    async def task_list(self, rt) -> dict:
        tasks, reauth = await self._call(rt.tasks.list_tasks)
        today = self._today(rt)
        return {"tasks": None if tasks is None else [
            self._task(t, today) for t in sorted(tasks, key=lambda t: (t["due"] is None, t["due"] or ""))],
            "reauth": reauth}

    @staticmethod
    async def _audit(rt, name: str, args: dict, result: dict) -> None:
        try:
            await asyncio.to_thread(rt.audit.record, "app", name, args=args, result=result, confirmation="direct")
        except Exception:
            log.exception("audit record failed")  # never hide a write that already happened

    async def create_task(self, rt, title: str, due: date | None) -> dict | None:
        task, _ = await self._call(rt.tasks.create_task, title, due)
        if task is None:
            return None
        out = self._task(task, self._today(rt))
        await self._audit(rt, "create_task", {"title": title, "due": due and due.isoformat()}, {"id": out["id"]})
        return out

    async def complete_task(self, rt, task_id: str) -> bool:
        task, _ = await self._call(rt.tasks.complete_task, task_id)
        if task is None:
            return False
        await self._audit(rt, "complete_task", {"id": task_id}, {"status": task.get("status")})
        return True

    # --- shopping: the hidden "Shopping" Google Tasks list ---
    async def shopping_list(self, rt) -> dict:
        def read():
            return rt.tasks.list_tasks(False, rt.tasks.shopping_list_id())

        items, reauth = await self._call(read)
        return {"items": None if items is None else [{"id": t["id"], "title": t.get("title") or ""} for t in items],
                "reauth": reauth}

    async def add_shopping(self, rt, title: str) -> dict | None:
        def add():
            return rt.tasks.create_task(title, None, rt.tasks.shopping_list_id())

        t, _ = await self._call(add)
        if t is None:
            return None
        await self._audit(rt, "add_shopping_items", {"items": [title]}, {"id": t["id"]})
        return {"id": t["id"], "title": t.get("title") or title}

    async def complete_shopping(self, rt, item_id: str) -> bool:
        def done():
            return rt.tasks.complete_task(item_id, rt.tasks.shopping_list_id())

        t, _ = await self._call(done)
        if t is None:
            return False
        await self._audit(rt, "complete_shopping_item", {"id": item_id}, {"status": t.get("status")})
        return True

    # --- knowledge graph control: (status, body); audit rows carry ids and counts only, never names or values ---
    async def _kg(self, rt, fn, *args) -> tuple[int, dict]:
        try:
            return 200, await asyncio.wait_for(asyncio.to_thread(fn, rt.kg, *args), self.source_timeout)
        except LookupError:
            return 404, {"error": "not_found"}
        except views.SelfEntity:
            return 409, {"error": "cannot_forget_self"}
        except Exception:
            log.exception("graph call failed")
            return 502, {"error": "unavailable"}

    async def graph_entities(self, rt):
        return await self._kg(rt, views.entity_list)

    async def graph_entity(self, rt, entity_id: int):
        return await self._kg(rt, views.entity_detail, entity_id)

    async def graph_forget_preview(self, rt, entity_id: int, mentions: bool):
        return await self._kg(rt, views.forget_preview, entity_id, mentions)

    async def graph_suppressions(self, rt):
        return await self._kg(rt, views.suppressions)

    async def graph_fact_correct(self, rt, fact_id: int, value: str):
        code, body = await self._kg(rt, views.fact_correct, fact_id, value)
        if code == 200:
            await self._audit(rt, "graph_fact_correct", {"fact_id": fact_id}, {"fact_id": body["id"]})
        return code, body

    async def graph_fact_delete(self, rt, fact_id: int):
        code, body = await self._kg(rt, views.fact_delete, fact_id)
        if code == 200:
            await self._audit(rt, "graph_fact_delete", {"fact_id": fact_id}, {"deleted": True})
        return code, body

    async def graph_forget(self, rt, entity_id: int, mentions: bool):
        code, body = await self._kg(rt, views.forget, entity_id, mentions)
        if code == 200:
            counts = {k: v for k, v in body.items() if isinstance(v, int)}  # `entity` is the name: leave it out
            await self._audit(rt, "graph_forget", {"entity_id": entity_id, "mentions": mentions}, counts)
        return code, body

    async def graph_suppression_lift(self, rt, key_type: str, key_hmac: str):
        code, body = await self._kg(rt, views.lift, key_type, key_hmac)
        if code == 200:
            await self._audit(rt, "graph_suppression_lift", {}, {"lifted": True})  # the hmac is a name fingerprint
        return code, body

    # --- mail (display only: never passed to the LLM) ---
    async def mail_list(self, rt, limit: int) -> dict:
        limit = max(1, min(limit, MAX_MAIL))
        # ponytail: search_emails caps at 20, so `more` is only detectable below the cap
        mail, reauth = await self._call(rt.gmail.search_emails, UNREAD_QUERY, min(limit + 1, MAX_MAIL))
        shown = None if mail is None else mail[:limit]
        return {"items": None if shown is None else [
            {"id": m["id"], "from": m.get("from") or "", "subject": m.get("subject") or "",
             "date": m.get("date") or "", "snippet": m.get("snippet") or ""} for m in shown],
            "count": 0 if shown is None else len(shown), "more": bool(mail) and len(mail) > limit, "reauth": reauth}

    async def mail_item(self, rt, message_id: str) -> dict | None:
        m, _ = await self._call(rt.gmail.read_email, message_id)
        if m is None:
            return None
        return {"id": m["id"], "from": m.get("from") or "", "subject": m.get("subject") or "",
                "date": m.get("date") or "", "body": m.get("body") or "", "truncated": bool(m.get("truncated"))}

    # --- notes ---
    async def note_list(self, rt) -> dict:
        notes, _ = await self._call(rt.notes.recent, 20)
        return {"notes": None if notes is None else [
            {"id": n["id"], "title": n["title"], "snippet": n.get("snippet") or "", "updated_at": n["updated_at"]}
            for n in notes]}

    async def note_item(self, rt, note_id: int) -> dict | None:
        n, _ = await self._call(rt.notes.get, note_id)
        return n and {k: n[k] for k in ("id", "title", "body", "updated_at")}

    # --- chat: same gate as channels/telegram.py ---
    @staticmethod
    def _card(it) -> dict:
        return {"interrupt_id": it.id, "summary": "\n".join(card_lines(it.value)),
                "tap_only": is_tap_only(it.value), "after_untrusted": bool(it.value.get("after_untrusted"))}

    async def _run(self, rt, decide) -> dict:
        """User's lock -> decide() as its own task: a dropped request never cancels a running graph step (confirmed
        writes get audited). Another user's step never waits on this lock."""
        await rt.lock.acquire()

        async def run():
            try:
                return await decide()
            finally:
                rt.lock.release()

        t = asyncio.create_task(run())
        self.steps.add(t)
        t.add_done_callback(self.steps.discard)
        return await asyncio.shield(t)

    async def _present(self, rt, graph_input) -> dict:
        cfg = thread_config(rt.user.id)
        try:
            result = await rt.graph.ainvoke(graph_input, cfg)
        except Exception as error:
            log.error("graph run failed")
            card = None
            try:  # re-offer a still-pending confirmation so the owner is not locked out
                interrupts = (await rt.graph.aget_state(cfg)).interrupts
                card = self._card(interrupts[0]) if interrupts else None
            except Exception:
                log.error("could not re-offer pending confirmation")
            return {"reply": str(error) if isinstance(error, MemoryContextError) else FAIL_TEXT,
                    "card": card, "client_actions": []}
        if result.get("__interrupt__"):
            return {"reply": None, "card": self._card(result["__interrupt__"][0]), "client_actions": []}
        return {"reply": "\n\n".join(turn_replies(result["messages"]) or [EMPTY_TEXT]), "card": None,
                "client_actions": result.get("client_actions") or []}

    async def chat(self, rt, text: str) -> dict:
        async def decide():
            pending = (await rt.graph.aget_state(thread_config(rt.user.id))).interrupts
            if pending:
                return {"reply": PENDING_TEXT, "card": self._card(pending[0]), "client_actions": []}
            return await self._present(rt, {"messages": [HumanMessage(text)]})

        return await self._run(rt, decide)

    async def confirm(self, rt, interrupt_id: str, decision: str) -> dict:
        async def decide():
            interrupts = (await rt.graph.aget_state(thread_config(rt.user.id))).interrupts
            if decision not in ("yes", "no") or not interrupts or interrupts[0].id != interrupt_id:
                return {"reply": HANDLED_TEXT, "card": None, "client_actions": []}
            return await self._present(rt, Command(resume=decision == "yes"))

        return await self._run(rt, decide)
