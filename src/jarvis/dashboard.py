import asyncio
import logging
from datetime import datetime, timedelta

from jarvis.google.auth import ReauthRequired
from jarvis.proactive.brief import speak_facts
from jarvis import weather
from jarvis.timeutil import now_local
from jarvis.voice.protocol import MAX_SPEAK_CHARS

log = logging.getLogger(__name__)
MAX_TASKS = 20
UNREAD_SHOWN = 5
NOTES_SHOWN = 5


class DashboardService:
    """Read-only snapshot for the app home screen. Every source fails alone (None) so one outage never blanks the screen."""

    def __init__(self, devices, calendar, tasks, gmail, notes, tz: str, source_timeout: float = 12.0, weather_city: str = "", store=None):
        self.devices, self.calendar, self.tasks, self.gmail, self.notes = devices, calendar, tasks, gmail, notes
        self.tz, self.source_timeout, self.weather_city, self.store = tz, source_timeout, weather_city, store

    async def authorized(self, authorization: str) -> bool:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return bool(token) and await asyncio.to_thread(self.devices.verify, token) is not None

    async def payload(self, now: datetime | None = None, coords: tuple[float, float] | None = None) -> dict:
        now = now or now_local(self.tz)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        reauth = False
        if coords and self.store:
            await asyncio.to_thread(weather.save_coords, self.store, *coords)  # the 7:30 brief reuses the latest fix
        elif self.store:
            coords = await asyncio.to_thread(weather.load_coords, self.store)

        async def get(fn, *args):
            nonlocal reauth
            try:
                # ponytail: a timed-out call keeps its worker thread until Google returns; fine for one owner
                return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout)
            except ReauthRequired:
                reauth = True
            except Exception:
                log.exception("dashboard source failed")
            return None

        events, all_tasks, mail, notes, wx = await asyncio.gather(
            get(self.calendar.list_events, day, day + timedelta(days=1)),
            get(self.tasks.list_tasks),
            get(self.gmail.search_emails, "is:unread in:inbox newer_than:7d", UNREAD_SHOWN + 1),
            get(self.notes.recent, NOTES_SHOWN),
            get(weather.fetch, self.weather_city, self.tz, now, coords))

        today = now.date().isoformat()
        overdue = None if all_tasks is None else [t for t in all_tasks if t["due"] and t["due"] < today]
        shown = None if mail is None else mail[:UNREAD_SHOWN]
        brief = speak_facts({"events": events, "overdue": overdue, "mail": shown, "weather": wx}, self.tz)
        return {
            "events": None if events is None else [
                {"summary": e.get("summary") or "(no title)", "start": e["start"], "end": e["end"],
                 "location": e.get("location"), "all_day": len(e["start"] or "") == 10} for e in events],
            "tasks": None if all_tasks is None else [
                {"title": t["title"] or "(no title)", "due": t["due"], "overdue": bool(t["due"] and t["due"] < today)}
                for t in sorted(all_tasks, key=lambda t: (t["due"] is None, t["due"] or ""))][:MAX_TASKS],
            "unread": None if mail is None else {
                "count": len(shown), "more": len(mail) > UNREAD_SHOWN,
                "items": [{"id": m.get("id"), "from": m.get("from") or "", "subject": m.get("subject") or ""} for m in shown]},
            "notes": notes,
            "brief": brief[:MAX_SPEAK_CHARS],
            "reauth": reauth,
        }
