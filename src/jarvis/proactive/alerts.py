import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis.timeutil import now_local, parse_dt


def _timed(events: list[dict]) -> list[dict]:
    return [e for e in events if not e["all_day"] and not e["declined"] and e["busy"]]


def find_conflicts(events: list[dict], tz: str) -> list[tuple[dict, dict]]:
    ev = sorted(_timed(events), key=lambda e: parse_dt(e["start"], tz))
    out = []
    for i, a in enumerate(ev):
        a_end = parse_dt(a["end"], tz)
        for b in ev[i + 1:]:
            if parse_dt(b["start"], tz) >= a_end:
                break  # sorted by start, so no later event overlaps a either
            out.append((a, b))
    return out


def leave_now_due(events: list[dict], now: datetime, lead_minutes: int, tz: str) -> list[dict]:
    limit = now + timedelta(minutes=lead_minutes)
    return [e for e in _timed(events) if e["location"] and now < parse_dt(e["start"], tz) <= limit]


def conflict_key(a: dict, b: dict) -> str:
    return "conflict:" + "|".join(sorted(f"{e['id']}@{e['start']}" for e in (a, b)))


def leave_key(e: dict) -> str:
    return f"leave:{e['id']}@{e['start']}"


def _c(s: str, cap: int = 100) -> str:
    return " ".join(s.split())[:cap]


def _when(e: dict, tz: str) -> str:
    return parse_dt(e["start"], tz).astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M")


def conflict_text(a: dict, b: dict, tz: str) -> str:
    return f"Calendar conflict: {_c(a['summary'])} ({_when(a, tz)}) overlaps {_c(b['summary'])} ({_when(b, tz)})."


def leave_text(e: dict, tz: str) -> str:
    return f"Time to leave: {_c(e['summary'])} starts at {_when(e, tz)[-5:]} at {_c(e['location'])}."


async def sweep(calendar, store, notifier, tz: str, lead_minutes: int, now: datetime | None = None) -> None:
    now = now or now_local(tz)
    events = await asyncio.to_thread(calendar.list_for_proactive, now, now + timedelta(hours=48))
    # ponytail: claim before sending = at most once; a failed send is not retried (notifier.both logs it)
    for a, b in find_conflicts(events, tz):
        if await asyncio.to_thread(store.claim_alert, conflict_key(a, b)):
            await notifier.telegram(conflict_text(a, b, tz))
    for e in leave_now_due(events, now, lead_minutes, tz):
        if await asyncio.to_thread(store.claim_alert, leave_key(e)):
            await notifier.both(leave_text(e, tz))
