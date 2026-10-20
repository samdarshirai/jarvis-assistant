import asyncio
import logging
from datetime import datetime, timedelta

from jarvis.google.auth import ReauthRequired
from jarvis.proactive.brief import speak_facts
from jarvis.tenant import auth_runtime
from jarvis import weather
from jarvis.timeutil import now_local
from jarvis.voice.protocol import MAX_SPEAK_CHARS

log = logging.getLogger(__name__)
MAX_TASKS = 20
UNREAD_SHOWN = 5
NOTES_SHOWN = 5


class DashboardService:
    """Read-only snapshot for the app home screen, built from the caller's runtime (`rt`, from authenticate()).
    Every source fails alone (None) so one outage never blanks the screen."""

    def __init__(self, devices, tenants, source_timeout: float = 12.0, weather_city: str = ""):
        self.devices, self.tenants, self.source_timeout, self.weather_city = devices, tenants, source_timeout, weather_city

    async def authenticate(self, authorization: str):
        """The caller's UserRuntime, or None (bad token, or a disabled or unknown user)."""
        return await auth_runtime(self.devices, self.tenants, authorization)

    async def payload(self, rt, now: datetime | None = None, coords: tuple[float, float] | None = None) -> dict:
        tz, store = rt.tz, rt.proactive
        now = now or now_local(tz)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        reauth = False
        if coords:
            await asyncio.to_thread(weather.save_coords, store, *coords)  # the 7:30 brief reuses the latest fix
        else:
            coords = await asyncio.to_thread(weather.load_coords, store)
        city = await asyncio.to_thread(store.get_state, "weather_city") or self.weather_city

        async def get(fn, *args):
            nonlocal reauth
            try:
                # ponytail: a timed-out call keeps its worker thread until Google returns; shared default executor across users
                return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout)
            except ReauthRequired:
                reauth = True
            except Exception:
                log.exception("dashboard source failed")
            return None

        events, all_tasks, mail, notes, wx = await asyncio.gather(
            get(rt.calendar.list_events, day, day + timedelta(days=1)),
            get(rt.tasks.list_tasks),
            get(rt.gmail.search_emails, "is:unread in:inbox newer_than:7d", UNREAD_SHOWN + 1),
            get(rt.notes.recent, NOTES_SHOWN),
            get(weather.fetch, city, tz, now, coords))

        today = now.date().isoformat()
        overdue = None if all_tasks is None else [t for t in all_tasks if t["due"] and t["due"] < today]
        shown = None if mail is None else mail[:UNREAD_SHOWN]
        brief = speak_facts({"events": events, "overdue": overdue, "mail": shown, "weather": wx}, tz)
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
