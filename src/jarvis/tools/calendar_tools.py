import logging
import unicodedata
from datetime import datetime, time, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from jarvis.google.gmail import clean_recipients
from jarvis.google.slots import Window, free_slots
from jarvis.timeutil import parse_dt
from jarvis.tools.registry import Registry, Tool

DT = "ISO 8601 date-time. No offset means the user's local time."
Scope = Literal["this", "all"]
Minutes = Annotated[int, Field(ge=0, le=40320)]
log = logging.getLogger(__name__)


class ListEventsArgs(BaseModel):
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    query: str | None = None


class CreateEventArgs(BaseModel):
    summary: str = Field(max_length=200)
    start: str = Field(description=DT)
    end: str = Field(description=DT)
    recurrence: list[str] | None = Field(default=None, description="RRULE strings, e.g. ['RRULE:FREQ=WEEKLY']")
    reminders: list[Minutes] | None = Field(default=None, max_length=5, description="Popup reminders as minutes before the start, e.g. [1440, 60] for a day and an hour before")
    attendees: list[str] | None = Field(default=None, max_length=10, description="Email addresses to invite; they are emailed only after the user confirms")


class UpdateEventArgs(BaseModel):
    event_id: str
    scope: Scope = Field(description="'this' = one occurrence, 'all' = whole series (summary only; start/end cannot change for a series)")
    summary: str | None = Field(default=None, max_length=200)
    start: str | None = Field(default=None, description=DT + " Only with scope='this'; not allowed for 'all'.")
    end: str | None = Field(default=None, description=DT + " Only with scope='this'; not allowed for 'all'.")
    reminders: list[Minutes] | None = Field(default=None, max_length=5, description="Replaces the event's popup reminders (minutes before the start)")
    add_attendees: list[str] | None = Field(default=None, max_length=10, description="Email addresses to add as invitees; they are emailed only after the user confirms")


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


def _when(v: str) -> str:
    return datetime.fromisoformat(v).strftime("%a %Y-%m-%d %H:%M") if "T" in v else datetime.fromisoformat(v).strftime("%a %Y-%m-%d")


def _range(start: str, end: str) -> str:
    s, e = _when(start), _when(end)
    return f"{s}-{e[-5:]}" if "T" in start and s[:14] == e[:14] else f"{s} to {e}"


def attendee_list(values) -> list[str]:
    """Cleaned, ASCII-only, case-insensitively de-duplicated addresses; the card and the call both use this."""
    out: list[str] = []
    for a in clean_recipients(", ".join(values)):
        if not a.isascii() or len(a) > 254 or any(unicodedata.category(c) in ("Cf", "Cc") for c in a):
            raise ValueError("attendee address must be plain ASCII")
        if a.casefold() not in {x.casefold() for x in out}:
            out.append(a)
    return out


def register_calendar_tools(registry: Registry, client, tz: str, sent_to=None) -> None:
    def span(start: str, end: str):
        s, e = parse_dt(start, tz), parse_dt(end, tz)
        if e <= s:
            raise ValueError("end must be after start")
        return s, e

    def minutes_text(m: int) -> str:
        if m == 0:
            return "at the start"
        if m % 1440 == 0:
            n = m // 1440
            return f"{n} day{'s' if n != 1 else ''} before"
        if m % 60 == 0:
            n = m // 60
            return f"{n} hour{'s' if n != 1 else ''} before"
        return f"{m} minutes before"

    def clip(s: str, cap: int = 60) -> str:
        return " ".join(str(s).split())[:cap]

    def reminder_line(reminders) -> str:
        if reminders is None:
            return ""
        return "\nReminders: " + (", ".join(minutes_text(m) for m in reminders) or "none")

    def invite_line(label: str, addresses) -> str:
        if not addresses:
            return ""
        parts = []
        for a in attendee_list(addresses):
            note = ""
            if sent_to is not None:
                try:
                    note = "" if sent_to(a) else " (never emailed by you)"
                except Exception:
                    log.exception("sent_to check failed")
                    note = " (could not check)"
            parts.append(a + note)
        return f"\n{label}: " + ", ".join(parts)

    def alternatives(start, duration):
        horizon = start + timedelta(days=7)
        slots = free_slots(client.busy(start, horizon), start, horizon, duration, [Window(tz, time(8, 0), time(20, 0))])
        return [_range(a.isoformat(), b.isoformat()) for a, b in slots]

    def conflict_note(start, end, exclude_id=None) -> str:
        try:
            hits = client.conflicts(start, end, exclude_id)
        except Exception:
            log.exception("conflict check failed")
            return "\n(could not check for conflicts)"
        if not hits:
            return ""
        names = "; ".join(f"'{clip(h['summary'])}' {_range(h['start'], h['end'])}" for h in hits[:3])
        if len(hits) > 3:
            names += f" and {len(hits) - 3} more"
        try:
            free = alternatives(start, end - start)
            tail = (" Free instead: " + "; ".join(free) + ".") if free else " No free slot found in the next 7 days between 08:00 and 20:00."
        except Exception:
            log.exception("alternatives lookup failed")
            tail = " (could not look up free slots)"
        return f"\nWarning: conflicts with {names}.{tail}"

    def list_events(start, end, query=None):
        return client.list_events(*span(start, end), query=query)

    def create_event(summary, start, end, recurrence=None, reminders=None, attendees=None):
        extra = {}
        if reminders is not None:
            extra["reminders"] = reminders
        if attendees:
            extra["attendees"] = attendee_list(attendees)
        return client.create_event(summary, *span(start, end), recurrence=recurrence, **extra)

    def update_event(event_id, scope, summary=None, start=None, end=None, reminders=None, add_attendees=None):
        if scope == "all" and (start or end):
            raise ValueError("start/end cannot be changed for a whole recurring series; change one occurrence "
                             "with scope='this', or delete and recreate the series")
        s = parse_dt(start, tz) if start else None
        e = parse_dt(end, tz) if end else None
        if s and e and e <= s:
            raise ValueError("end must be after start")
        extra = {}
        if reminders is not None:
            extra["reminders"] = reminders
        if add_attendees:
            extra["add_attendees"] = attendee_list(add_attendees)
        return client.update_event(event_id, scope, summary=summary, start=s, end=e, **extra)

    def delete_event(event_id, scope):
        return client.delete_event(event_id, scope)

    def find_free_slots(duration_minutes, range_start, range_end, windows=()):
        s, e = span(range_start, range_end)
        ws = [Window(w["tz"], time.fromisoformat(w["start"]), time.fromisoformat(w["end"]))
              for w in (x if isinstance(x, dict) else x.model_dump() for x in windows)]
        slots = free_slots(client.busy(s, e), s, e, timedelta(minutes=duration_minutes), ws)
        return [{"start": a.astimezone(s.tzinfo).isoformat(), "end": b.astimezone(s.tzinfo).isoformat()}
                for a, b in slots]

    def describe_create(a):
        s, e = span(a["start"], a["end"])
        # ponytail: a recurring series is checked at its first occurrence only
        # invitee lines come first so a clipped card can never hide who gets an email
        return (f"Create '{clip(a['summary'], 120)}' {_range(s.isoformat(), e.isoformat())}"
                + invite_line("Invites emailed to", a.get("attendees")) + reminder_line(a.get("reminders")) + conflict_note(s, e))

    def current(event_id):
        ev = client.get_event(event_id)
        return f"'{clip(ev['summary'], 120)}' ({_range(ev['start'], ev['end'])})"

    def add_invitees_lines(a) -> str:
        if not a.get("add_attendees"):
            return ""
        wanted = attendee_list(a["add_attendees"])
        try:
            existing = client.attendee_emails(a["event_id"], a["scope"])
        except Exception:
            log.exception("existing attendees lookup failed")
            return invite_line("Adds invitees (they are emailed)", wanted) + "\n(could not check existing guests)"
        have = {x.casefold() for x in existing}
        new = [x for x in wanted if x.casefold() not in have]
        if not new:  # mirrors CalendarClient.update_event: nothing new, nothing sent
            return "\nAdds invitees: none new (already invited)"
        text = invite_line("Adds invitees (they are emailed)", new)
        if existing:
            shown = ", ".join(existing[:10]) + (f" and {len(existing) - 10} more" if len(existing) > 10 else "")
            text += f"\nExisting guests also get an update email: {shown}"
        return text

    def describe_update(a):
        ev = client.get_event(a["event_id"])
        changes = []
        if "summary" in a:
            changes.append(f"title -> '{clip(a['summary'], 120)}'")
        for k in ("start", "end"):
            if k in a:
                changes.append(f"{k} -> {_when(parse_dt(a[k], tz).isoformat())}")
        series = ", whole recurring series" if a["scope"] == "all" else ""
        text = f"Change '{clip(ev['summary'], 120)}' ({_range(ev['start'], ev['end'])}){series}: " + "; ".join(changes)
        text += add_invitees_lines(a) + reminder_line(a.get("reminders"))
        if a["scope"] != "all" and ("start" in a or "end" in a):
            old_s, old_e = parse_dt(ev["start"], tz), parse_dt(ev["end"], tz)
            s = parse_dt(a["start"], tz) if "start" in a else old_s
            e = parse_dt(a["end"], tz) if "end" in a else (s + (old_e - old_s))
            if e > s:
                text += conflict_note(s, e, a["event_id"])
        return text

    def describe_delete(a):
        return f"Delete {current(a['event_id'])}" + (" - the whole recurring series" if a["scope"] == "all" else "")

    def ev(r):
        return f"'{r['summary']}' {_range(r['start'], r['end'])}"

    dones = {"create_event": lambda r: f"All set, I've added {ev(r)}.", "update_event": lambda r: f"Done, I've updated {ev(r)}.",
             "delete_event": lambda r: "Okay, that's been removed from your calendar."}
    describers = {"create_event": describe_create, "update_event": describe_update, "delete_event": describe_delete}

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
                          fn=fn, needs_confirm=confirm, describe=describers.get(name), done=dones.get(name)))
