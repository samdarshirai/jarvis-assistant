import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from jarvis.agent.graph import turn_replies
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, HANDLED_TEXT, PENDING_TEXT, THREAD
from jarvis.dashboard import DashboardService
from jarvis.google.auth import ReauthRequired
from jarvis.timeutil import now_local
from jarvis.voice.ws import card_lines, is_tap_only

log = logging.getLogger(__name__)
UNREAD_QUERY = "is:unread in:inbox newer_than:7d"
MAX_MAIL = 20


class AppService:
    """Device-token REST endpoints behind the app screens. Reads fail per source (None); chat mirrors the Telegram gate."""

    authorized = DashboardService.authorized  # same bearer + constant-time verify; only needs self.devices

    def __init__(self, devices, calendar, tasks, gmail, notes, graph, lock: asyncio.Lock, audit, tz: str,
                 source_timeout: float = 12.0):
        self.devices, self.calendar, self.tasks, self.gmail, self.notes = devices, calendar, tasks, gmail, notes
        self.graph, self.lock, self.audit, self.tz, self.source_timeout = graph, lock, audit, tz, source_timeout
        self.steps: set[asyncio.Task] = set()  # chat graph steps; drained in lifespan teardown

    async def _call(self, fn, *args):
        """(value, reauth); value is None when the source failed."""
        try:
            # ponytail: a timed-out call keeps its worker thread until Google returns; fine for one owner
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout), False
        except ReauthRequired:
            return None, True
        except Exception:
            log.exception("app source failed")
            return None, False

    def _today(self) -> str:
        return now_local(self.tz).date().isoformat()

    def _task(self, t: dict, today: str) -> dict:
        due = t.get("due")
        return {"id": t["id"], "title": t.get("title") or "(no title)", "due": due, "overdue": bool(due and due < today)}

    # --- calendar / tasks ---
    async def calendar_events(self, start: date, days: int) -> dict:
        days = max(1, min(days, 14))
        a = datetime.combine(start, time.min, tzinfo=ZoneInfo(self.tz))
        events, reauth = await self._call(self.calendar.list_events, a, a + timedelta(days=days))
        return {"events": None if events is None else [
            {"id": e["id"], "summary": e.get("summary") or "(no title)", "start": e["start"], "end": e["end"],
             "location": e.get("location"), "all_day": len(e["start"] or "") == 10} for e in events], "reauth": reauth}

    async def task_list(self) -> dict:
        tasks, reauth = await self._call(self.tasks.list_tasks)
        today = self._today()
        return {"tasks": None if tasks is None else [
            self._task(t, today) for t in sorted(tasks, key=lambda t: (t["due"] is None, t["due"] or ""))],
            "reauth": reauth}

    async def _audit(self, name: str, args: dict, result: dict) -> None:
        try:
            await asyncio.to_thread(self.audit.record, "app", name, args=args, result=result, confirmation="direct")
        except Exception:
            log.exception("audit record failed")  # never hide a write that already happened

    async def create_task(self, title: str, due: date | None) -> dict | None:
        task, _ = await self._call(self.tasks.create_task, title, due)
        if task is None:
            return None
        out = self._task(task, self._today())
        await self._audit("create_task", {"title": title, "due": due and due.isoformat()}, {"id": out["id"]})
        return out

    async def complete_task(self, task_id: str) -> bool:
        task, _ = await self._call(self.tasks.complete_task, task_id)
        if task is None:
            return False
        await self._audit("complete_task", {"id": task_id}, {"status": task.get("status")})
        return True

    # --- mail (display only: never passed to the LLM) ---
    async def mail_list(self, limit: int) -> dict:
        limit = max(1, min(limit, MAX_MAIL))
        # ponytail: search_emails caps at 20, so `more` is only detectable below the cap
        mail, reauth = await self._call(self.gmail.search_emails, UNREAD_QUERY, min(limit + 1, MAX_MAIL))
        shown = None if mail is None else mail[:limit]
        return {"items": None if shown is None else [
            {"id": m["id"], "from": m.get("from") or "", "subject": m.get("subject") or "",
             "date": m.get("date") or "", "snippet": m.get("snippet") or ""} for m in shown],
            "count": 0 if shown is None else len(shown), "more": bool(mail) and len(mail) > limit, "reauth": reauth}

    async def mail_item(self, message_id: str) -> dict | None:
        m, _ = await self._call(self.gmail.read_email, message_id)
        if m is None:
            return None
        return {"id": m["id"], "from": m.get("from") or "", "subject": m.get("subject") or "",
                "date": m.get("date") or "", "body": m.get("body") or "", "truncated": bool(m.get("truncated"))}

    # --- notes ---
    async def note_list(self) -> dict:
        notes, _ = await self._call(self.notes.recent, 20)
        return {"notes": None if notes is None else [
            {"id": n["id"], "title": n["title"], "snippet": n.get("snippet") or "", "updated_at": n["updated_at"]}
            for n in notes]}

    async def note_item(self, note_id: int) -> dict | None:
        n, _ = await self._call(self.notes.get, note_id)
        return n and {k: n[k] for k in ("id", "title", "body", "updated_at")}

    # --- chat: same gate as channels/telegram.py ---
    @staticmethod
    def _card(it) -> dict:
        return {"interrupt_id": it.id, "summary": "\n".join(card_lines(it.value)),
                "tap_only": is_tap_only(it.value), "after_untrusted": bool(it.value.get("after_untrusted"))}

    async def _run(self, decide) -> dict:
        """Lock -> decide() as its own task: a dropped request never cancels a running graph step (confirmed writes get audited)."""
        await self.lock.acquire()

        async def run():
            try:
                return await decide()
            finally:
                self.lock.release()

        t = asyncio.create_task(run())
        self.steps.add(t)
        t.add_done_callback(self.steps.discard)
        return await asyncio.shield(t)

    async def _present(self, graph_input) -> dict:
        try:
            result = await self.graph.ainvoke(graph_input, THREAD)
        except Exception:
            log.exception("graph run failed")
            card = None
            try:  # re-offer a still-pending confirmation so the owner is not locked out
                interrupts = (await self.graph.aget_state(THREAD)).interrupts
                card = self._card(interrupts[0]) if interrupts else None
            except Exception:
                log.exception("could not re-offer pending confirmation")
            return {"reply": FAIL_TEXT, "card": card, "client_actions": []}
        if result.get("__interrupt__"):
            return {"reply": None, "card": self._card(result["__interrupt__"][0]), "client_actions": []}
        return {"reply": "\n\n".join(turn_replies(result["messages"]) or [EMPTY_TEXT]), "card": None,
                "client_actions": result.get("client_actions") or []}

    async def chat(self, text: str) -> dict:
        async def decide():
            pending = (await self.graph.aget_state(THREAD)).interrupts
            if pending:
                return {"reply": PENDING_TEXT, "card": self._card(pending[0]), "client_actions": []}
            return await self._present({"messages": [HumanMessage(text)]})

        return await self._run(decide)

    async def confirm(self, interrupt_id: str, decision: str) -> dict:
        async def decide():
            interrupts = (await self.graph.aget_state(THREAD)).interrupts
            if decision not in ("yes", "no") or not interrupts or interrupts[0].id != interrupt_id:
                return {"reply": HANDLED_TEXT, "card": None, "client_actions": []}
            return await self._present(Command(resume=decision == "yes"))

        return await self._run(decide)
