"""Short-term memory and recall tools, offered in every domain (domain "*").

remember_for_now saves a time-bound note without a confirmation: the reply says so and the app shows it with Undo.
It is refused while untrusted email or web text is in play, so a message or page can never write the user's memory."""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, StringConstraints

from jarvis.memory import search
from jarvis.memory.store import KINDS, MAX_TEXT, MemoryStore, item_json
from jarvis.tools.registry import Registry, Tool

OFF_RECORD = {"error": "This conversation is off the record: nothing is saved. Tell the user."}


class RememberForNowArgs(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=MAX_TEXT)] = Field(
        description="The note in the user's terms, written about them, e.g. 'In Pune until Friday', "
                    "'Waiting for the plumber to call back', 'Promised Anna the slides by Thursday'")
    kind: Literal[KINDS] = Field(  # type: ignore[valid-type]
        default="context", description="context: a temporary situation; plan: something coming up; "
                                       "commitment: something the user promised or must do")
    until: date | None = Field(
        default=None, description="Last day it matters, as YYYY-MM-DD (resolve 'till Friday' from today). "
                                  "Omit when unknown: it is kept for a week")


class ForgetShortTermArgs(BaseModel):
    item_id: int | None = Field(default=None, ge=1, description="The id shown in the short-term notes; omit for "
                                                                "the one just saved in this conversation")


class RecallArgs(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = Field(
        description="What to look for, in plain words, e.g. 'flights to Delhi' or 'Anna's new job'")
    since: date | None = Field(default=None, description="Only memories from this day on (YYYY-MM-DD)")
    until: date | None = Field(default=None, description="Only memories up to this day (YYYY-MM-DD)")


def _day_end(d: date, tz: str) -> datetime:
    return datetime.combine(d, time(23, 59), tzinfo=ZoneInfo(tz))


def _label(when: datetime, tz: str) -> str:
    return when.astimezone(ZoneInfo(tz)).strftime("%a %-d %b")


def register_memory_tools(registry: Registry, memory: MemoryStore, tz: str, *, embedder=None) -> None:
    def conversation(ctx) -> tuple[int | None, bool]:
        thread = (ctx or {}).get("thread_id")
        found = memory.conversation_for(thread) if thread else None
        return found if found else (None, False)

    def remember_for_now(text: str, kind: str = "context", until: date | None = None, _ctx=None) -> dict:
        conv_id, off = conversation(_ctx)
        if off:
            return OFF_RECORD
        item, created = memory.add_item(text, kind, _day_end(until, tz) if until else None, origin="tool",
                                        conversation_id=conv_id)
        until_label = _label(item.expires_at, tz)
        return {"saved": True, "already_known": not created, "id": item.id, "until": until_label,
                "memory_event": {"type": "memory_saved", **item_json(item), "until": until_label}}

    def forget_short_term(item_id: int | None = None, _ctx=None) -> dict:
        if item_id is None:
            conv_id, _ = conversation(_ctx)
            item = memory.latest_in(conv_id) if conv_id else None
            if item is None:
                return {"error": "Nothing was saved in this conversation lately. Ask which note to forget."}
            item_id = item.id
        if not memory.delete(item_id):
            return {"error": "No such short-term note."}
        return {"forgotten": True, "id": item_id, "memory_event": {"type": "memory_forgotten", "id": item_id}}

    async def recall(query: str = "", since: date | None = None, until: date | None = None, _ctx=None) -> dict:
        a = datetime.combine(since, time.min, tzinfo=ZoneInfo(tz)) if since else None
        b = _day_end(until, tz) + timedelta(minutes=1) if until else None
        hits = await search.recall(memory.pool, memory.user_id, query, embedder, a, b)
        if not hits:
            return {"results": [], "note": "Nothing found in memory. Say so; never guess what was said before."}
        return {"results": [search.hit_json(h) for h in hits]}

    registry.add(Tool(
        name="remember_for_now", domain="*", args_schema=RememberForNowArgs, fn=remember_for_now, needs_confirm=False,
        refuse_after_untrusted=True, context=True,
        description="Save a SHORT-TERM note about the user: a temporary situation (travel, being ill, a visitor), a plan "
                    "coming up, something they are waiting for, or a promise they made. It expires on its own. Use it "
                    "whenever the user mentions such a thing, without asking, then say briefly that you noted it "
                    "(e.g. 'Noted, till Friday'). Lasting facts (where they live, who someone is) are NOT short-term."))
    registry.add(Tool(
        name="forget_short_term", domain="*", args_schema=ForgetShortTermArgs, fn=forget_short_term,
        needs_confirm=False, refuse_after_untrusted=True, context=True,
        description="Delete a short-term note: the one just saved ('forget that', 'no, don't note that') or one from "
                    "the short-term notes by its id. Use it too when such a note is no longer true."))
    registry.add(Tool(
        name="recall", domain="*", args_schema=RecallArgs, fn=recall, needs_confirm=False, context=True,
        description="Search everything remembered: past conversations, short-term notes (also expired ones), saved "
                    "facts and notes. Use it when the user refers to something from before that is not in this "
                    "conversation or the memory block ('last time', 'the flights we looked at', 'what did I say "
                    "about...'). For 'what did we talk about yesterday' pass only since/until."))
