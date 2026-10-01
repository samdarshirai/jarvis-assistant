from datetime import time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.google.slots import Window, free_slots
from jarvis.timeutil import parse_dt
from jarvis.tools.registry import Registry, Tool

DT = "ISO 8601 date-time. No offset means the user's local time."
Scope = Literal["this", "all"]


class ListEventsArgs(BaseModel):
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    query: str | None = None


class CreateEventArgs(BaseModel):
    summary: str
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    recurrence: list[str] | None = Field(default=None, description="RRULE strings, e.g. ['RRULE:FREQ=WEEKLY']")


class UpdateEventArgs(BaseModel):
    event_id: str
    scope: Scope = Field(description="'this' = one occurrence, 'all' = whole series (summary only; start/end cannot change for a series)")
    summary: str | None = None
    start: str | None = Field(default=None, description=DT + " Only with scope='this'; not allowed for 'all'.")
    end: str | None = Field(default=None, description=DT + " Only with scope='this'; not allowed for 'all'.")


class DeleteEventArgs(BaseModel):
    event_id: str
    scope: Scope


class WindowArg(BaseModel):
    tz: str = Field(description="IANA zone, e.g. Asia/Kolkata")
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")


class FreeSlotsArgs(BaseModel):
    duration_minutes: int = Field(gt=0)
    range_start: str = Field(description=DT)
    range_end: str = Field(description=DT)
    windows: list[WindowArg] = Field(default_factory=list,
                                     description="Slot must fit inside every window (local time in its zone)")


def register_calendar_tools(registry: Registry, client, tz: str) -> None:
    def span(start: str, end: str):
        s, e = parse_dt(start, tz), parse_dt(end, tz)
        if e <= s:
            raise ValueError("end must be after start")
        return s, e

    def list_events(start, end, query=None):
        return client.list_events(*span(start, end), query=query)

    def create_event(summary, start, end, recurrence=None):
        return client.create_event(summary, *span(start, end), recurrence=recurrence)

    def update_event(event_id, scope, summary=None, start=None, end=None):
        if scope == "all" and (start or end):
            raise ValueError("start/end cannot be changed for a whole recurring series; change one occurrence "
                             "with scope='this', or delete and recreate the series")
        s = parse_dt(start, tz) if start else None
        e = parse_dt(end, tz) if end else None
        if s and e and e <= s:
            raise ValueError("end must be after start")
        return client.update_event(event_id, scope, summary=summary, start=s, end=e)

    def delete_event(event_id, scope):
        return client.delete_event(event_id, scope)

    def find_free_slots(duration_minutes, range_start, range_end, windows=()):
        s, e = span(range_start, range_end)
        ws = [Window(w["tz"], time.fromisoformat(w["start"]), time.fromisoformat(w["end"]))
              for w in (x if isinstance(x, dict) else x.model_dump() for x in windows)]
        slots = free_slots(client.busy(s, e), s, e, timedelta(minutes=duration_minutes), ws)
        return [{"start": a.astimezone(s.tzinfo).isoformat(), "end": b.astimezone(s.tzinfo).isoformat()}
                for a, b in slots]

    for name, desc, schema, fn, confirm in [
        ("list_events", "List or search calendar events in a date range.", ListEventsArgs, list_events, False),
        ("find_free_slots", "Find free meeting slots, optionally constrained by time windows in several zones.",
         FreeSlotsArgs, find_free_slots, False),
        ("create_event", "Create a calendar event (optionally recurring).", CreateEventArgs, create_event, True),
        ("update_event", "Change or move an event or one occurrence of a recurring event.",
         UpdateEventArgs, update_event, True),
        ("delete_event", "Delete an event or a whole recurring series.", DeleteEventArgs, delete_event, True),
    ]:
        registry.add(Tool(name=name, domain="calendar", description=desc, args_schema=schema,
                          fn=fn, needs_confirm=confirm))
