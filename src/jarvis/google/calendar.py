from datetime import datetime
from typing import Any, Callable

from googleapiclient.errors import HttpError

from jarvis.timeutil import parse_dt


def _slim(e: dict) -> dict:
    s, en = e.get("start", {}), e.get("end", {})
    return {
        "id": e["id"],
        "recurring_event_id": e.get("recurringEventId"),
        "summary": e.get("summary"),
        "start": s.get("dateTime") or s.get("date"),
        "end": en.get("dateTime") or en.get("date"),
        "location": e.get("location"),
    }


def _proactive(e: dict) -> dict:
    s, en = e.get("start", {}), e.get("end", {})
    me = next((a for a in e.get("attendees", []) if a.get("self")), {})
    private = (e.get("extendedProperties") or {}).get("private") or {}
    return {
        "id": e["id"],
        "summary": e.get("summary") or "",
        "start": s.get("dateTime") or s.get("date"),
        "end": en.get("dateTime") or en.get("date"),
        "location": e.get("location"),
        "all_day": "dateTime" not in s,
        "declined": me.get("responseStatus") == "declined",
        "busy": e.get("transparency", "opaque") != "transparent",
        "source_message": private.get("jarvisMsgId"),
    }


def _reminders(minutes: list[int]) -> dict:
    return {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in minutes]}


class CalendarClient:
    def __init__(self, service_factory: Callable[[], Any], tz: str):
        self._svc = service_factory
        self.tz = tz

    def _when(self, dt: datetime) -> dict:
        return {"dateTime": dt.isoformat(), "timeZone": self.tz}

    def _target(self, event_id: str, scope: str) -> str:
        if scope == "this":
            return event_id
        ev = self._svc().events().get(calendarId="primary", eventId=event_id).execute()
        return ev.get("recurringEventId", event_id)

    def get_event(self, event_id: str) -> dict:
        return _slim(self._svc().events().get(calendarId="primary", eventId=event_id).execute())

    def list_events(self, start: datetime, end: datetime, query: str | None = None, limit: int = 50) -> list[dict]:
        resp = self._svc().events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(), q=query,
            singleEvents=True, orderBy="startTime", maxResults=limit).execute()
        return [_slim(e) for e in resp.get("items", [])]

    def list_for_proactive(self, start: datetime, end: datetime) -> list[dict]:
        """Events with the flags the scheduled jobs need (all-day, declined, busy, which email created it)."""
        resp = self._svc().events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100).execute()
        return [_proactive(e) for e in resp.get("items", []) if e.get("status") != "cancelled"]

    def conflicts(self, start: datetime, end: datetime, exclude_id: str | None = None) -> list[dict]:
        """Busy, timed, non-declined events that strictly overlap [start, end); back-to-back does not count."""
        out = []
        for e in self.list_for_proactive(start, end):
            if e["id"] == exclude_id or e["all_day"] or e["declined"] or not e["busy"]:
                continue
            if parse_dt(e["start"], self.tz) < end and start < parse_dt(e["end"], self.tz):
                out.append(e)
        return out

    def create_auto_event(self, event_id, summary, start, end, location, reminder_minutes, message_id, description) -> dict | None:
        """Insert with a caller-chosen id so a retry cannot duplicate: 409 means an earlier attempt already created it."""
        body = {"id": event_id, "summary": summary, "description": description,
                "start": self._when(start), "end": self._when(end),
                "reminders": {"useDefault": False,
                              "overrides": [{"method": "popup", "minutes": m} for m in reminder_minutes]},
                "extendedProperties": {"private": {"jarvisMsgId": message_id}}}
        if location:
            body["location"] = location
        try:
            return _slim(self._svc().events().insert(calendarId="primary", body=body).execute())
        except HttpError as e:
            if e.resp.status == 409:
                return None
            raise

    def create_event(self, summary, start, end, recurrence: list[str] | None = None,
                     reminders: list[int] | None = None, attendees: list[str] | None = None) -> dict:
        body = {"summary": summary, "start": self._when(start), "end": self._when(end)}
        if recurrence:
            body["recurrence"] = recurrence
        if reminders is not None:
            body["reminders"] = _reminders(reminders)
        kwargs = {}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
            kwargs["sendUpdates"] = "all"  # the only way an invite leaves: attendees exist only after the confirm tap
        return _slim(self._svc().events().insert(calendarId="primary", body=body, **kwargs).execute())

    def update_event(self, event_id, scope, summary=None, start=None, end=None,
                     reminders: list[int] | None = None, add_attendees: list[str] | None = None) -> dict:
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = self._when(start)
        if end is not None:
            body["end"] = self._when(end)
        if reminders is not None:
            body["reminders"] = _reminders(reminders)
        target = self._target(event_id, scope)
        kwargs = {}
        if add_attendees:
            current = self._svc().events().get(calendarId="primary", eventId=target).execute().get("attendees", [])
            have = {a.get("email", "").casefold() for a in current}
            new = [{"email": a} for a in add_attendees if a.casefold() not in have]
            if new:  # add-only: existing attendees (and their responses) are kept
                body["attendees"] = current + new
                kwargs["sendUpdates"] = "all"
        return _slim(self._svc().events().patch(calendarId="primary", eventId=target, body=body, **kwargs).execute())

    def delete_event(self, event_id, scope) -> dict:
        target = self._target(event_id, scope)
        self._svc().events().delete(calendarId="primary", eventId=target).execute()
        return {"deleted": target}

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        resp = self._svc().freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]}).execute()
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
                for b in resp["calendars"]["primary"]["busy"]]
