import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from jarvis.agent.graph import turn_replies
from jarvis.conversations import IDLE, conversation_json, messages_json
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, HANDLED_TEXT, PENDING_TEXT
from jarvis.google.auth import ReauthRequired
from jarvis.kg import review, views
from jarvis.llm import MemoryContextError
from jarvis.memory.store import item_json
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
        except ValueError:  # a rejected value or kind (candidate edits): the caller's input, not an outage
            return 400, {"error": "bad_request"}
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

    # --- candidate review: facts the extractor proposed, decided by the user's tap (the tap is the confirmation) ---
    async def graph_candidates(self, rt):
        return await self._kg(rt, review.candidates)

    async def graph_candidates_count(self, rt):
        code, body = await self._kg(rt, review.count)
        # "enabled" stays for app builds that hide the inbox when it is false (the extractor is always on now)
        return code, ({**body, "enabled": True} if code == 200 else body)

    async def graph_candidate_approve(self, rt, cand_id: int, edits: dict):
        code, body = await self._kg(rt, lambda kg: review.approve(kg, cand_id, **edits))
        if code == 200 and "choice" in body:
            return 409, {"error": "ambiguous_subject", **body}  # nothing was written: the user picks, then retries
        if code == 200:
            await self._audit(rt, "graph_candidate_approve", {"candidate_id": cand_id},
                              {"entity_id": body["entity"]["id"], "created": body["created"], "fact_ids": body["fact_ids"]})
        return code, body

    async def graph_candidate_dismiss(self, rt, cand_id: int):
        code, body = await self._kg(rt, review.dismiss, cand_id)
        if code == 200:
            await self._audit(rt, "graph_candidate_dismiss", {"candidate_id": cand_id}, {"dismissed": True})
        return code, body

    async def graph_candidates_bulk(self, rt, ids: list[int], decision: str):
        code, body = await self._kg(rt, review.bulk, ids, decision)
        if code == 200:
            await self._audit(rt, "graph_candidates_bulk", {"ids": ids, "decision": decision},
                              {k: len(v) for k, v in body.items()})
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

    async def _present(self, rt, graph_input, cfg) -> dict:
        cfg = {**cfg, "tags": ["app"], "metadata": {"channel": "app"}, "run_name": "jarvis-turn"}
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
                "client_actions": result.get("client_actions") or [],
                **({"memory_events": ev} if (ev := result.get("memory_events")) else {})}  # the Noted chip, Undo

    async def chat(self, rt, text: str) -> dict:
        async def decide():
            conv = await asyncio.to_thread(rt.conversations.current, "chat")
            await asyncio.to_thread(rt.conversations.touch, conv.id, text)
            conv = await asyncio.to_thread(rt.conversations.get, conv.id)
            cfg = thread_config(conv.thread_id)
            pending = (await rt.graph.aget_state(cfg)).interrupts
            out = ({"reply": PENDING_TEXT, "card": self._card(pending[0]), "client_actions": []} if pending
                   else await self._present(rt, {"messages": [HumanMessage(text)]}, cfg))
            return {**out, "conversation": conversation_json(conv)}

        return await self._run(rt, decide)

    async def confirm(self, rt, interrupt_id: str, decision: str, conversation_id: int | None = None) -> dict:
        async def decide():
            handled = {"reply": HANDLED_TEXT, "card": None, "client_actions": []}
            if conversation_id is None:
                conv = await asyncio.to_thread(rt.conversations.open_for, "chat")
            else:
                conv = await asyncio.to_thread(rt.conversations.get, conversation_id)
            if conv is None:
                return handled
            cfg = thread_config(conv.thread_id)
            interrupts = (await rt.graph.aget_state(cfg)).interrupts
            if decision not in ("yes", "no") or not interrupts or interrupts[0].id != interrupt_id:
                return {**handled, "conversation": conversation_json(conv)}
            return {**await self._present(rt, Command(resume=decision == "yes"), cfg),
                    "conversation": conversation_json(conv)}

        return await self._run(rt, decide)

    # --- conversations: (status, body) where a status can differ ---
    async def conversations(self, rt) -> dict:
        rows = await asyncio.to_thread(rt.conversations.list)
        memory = getattr(rt, "memory", None)
        summaries = await asyncio.to_thread(memory.summaries_for, [c.id for c in rows]) if memory else {}
        return {"conversations": [{**conversation_json(c), "summary": summaries.get(c.id)} for c in rows]}

    async def new_conversation(self, rt, off_record: bool = False) -> dict:
        conv = await asyncio.to_thread(rt.conversations.start, "chat", off_record) if off_record else \
            await asyncio.to_thread(rt.conversations.start, "chat")
        return {"conversation": {**conversation_json(conv), "off_record": off_record}}

    # --- short-term memory: the Memory screen's "Right now" list and the chat's Undo ---
    async def _memory(self, rt, fn, *args):
        """(status, body) for a MemoryStore call: 404 unknown or foreign, 400 bad input, 502 the database failed."""
        if getattr(rt, "memory", None) is None:
            return 503, {"error": "unavailable"}
        try:
            return 200, await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout)
        except LookupError:
            return 404, {"error": "not_found"}
        except ValueError:
            return 400, {"error": "bad_request"}
        except Exception:
            log.exception("memory call failed")
            return 502, {"error": "unavailable"}

    async def memory_items(self, rt):
        return await self._memory(rt, lambda: {"items": [item_json(i) for i in rt.memory.active()]})

    async def memory_item_update(self, rt, item_id: int, text: str | None, until: date | None):
        end = datetime.combine(until, time(23, 59), tzinfo=ZoneInfo(rt.tz)) if until else None
        code, body = await self._memory(rt, lambda: {"item": item_json(rt.memory.update(item_id, text=text, expires_at=end))})
        if code == 200:
            await self._audit(rt, "memory_item_update", {"item_id": item_id}, {"ok": True})
        return code, body

    async def memory_item_delete(self, rt, item_id: int):
        def go():
            if not rt.memory.delete(item_id):
                raise LookupError("item")
            return {"ok": True}

        code, body = await self._memory(rt, go)
        if code == 200:
            await self._audit(rt, "memory_item_delete", {"item_id": item_id}, {"deleted": True})
        return code, body

    async def memory_item_promote(self, rt, item_id: int):
        code, body = await self._memory(rt, rt.memory.promote, item_id, rt.kg)
        if code == 200:
            await self._audit(rt, "memory_item_promote", {"item_id": item_id}, body)
        return code, body

    async def _detail(self, rt, conv) -> dict:
        out = {"conversation": conversation_json(conv), "messages": [], "card": None}
        if rt.graph is None:  # no LLM key yet: the row is still listed, its thread is unreadable
            return out
        try:
            interrupts = (await rt.graph.aget_state(thread_config(conv.thread_id))).interrupts
            out["card"] = self._card(interrupts[0]) if interrupts else None
        except Exception:
            log.error("could not read pending confirmation")
        try:
            out["messages"] = await messages_json(rt.graph, conv.thread_id)
        except Exception:
            log.error("could not read conversation messages")
        return out

    async def current_chat(self, rt) -> dict:
        """The open chat conversation if it is still within IDLE (the rule current() uses); never starts one."""
        conv = await asyncio.to_thread(rt.conversations.open_for, "chat")
        if conv is None or rt.conversations.clock() - conv.last_at > IDLE:
            return {"conversation": None, "messages": [], "card": None}
        return await self._detail(rt, conv)

    async def resume_conversation(self, rt, conv_id: int) -> tuple[int, dict]:
        async def decide():
            if await asyncio.to_thread(rt.conversations.get, conv_id) is None:
                return 404, {"error": "not_found"}
            conv = await asyncio.to_thread(rt.conversations.resume, conv_id, "chat")
            if conv is None:
                return 409, {"error": "in_progress"}
            return 200, await self._detail(rt, conv)

        return await self._run(rt, decide)

    async def conversation_detail(self, rt, conv_id: int) -> tuple[int, dict]:
        conv = await asyncio.to_thread(rt.conversations.get, conv_id)
        if conv is None:
            return 404, {"error": "not_found"}
        return 200, await self._detail(rt, conv)
