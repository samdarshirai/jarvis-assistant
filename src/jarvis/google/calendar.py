from datetime import datetime
from typing import Any, Callable

from googleapiclient.errors import HttpError


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

    def create_event(self, summary, start, end, recurrence: list[str] | None = None) -> dict:
        body = {"summary": summary, "start": self._when(start), "end": self._when(end)}
        if recurrence:
            body["recurrence"] = recurrence
        return _slim(self._svc().events().insert(calendarId="primary", body=body).execute())

    def update_event(self, event_id, scope, summary=None, start=None, end=None) -> dict:
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = self._when(start)
        if end is not None:
            body["end"] = self._when(end)
        target = self._target(event_id, scope)
        return _slim(self._svc().events().patch(calendarId="primary", eventId=target, body=body).execute())

    def delete_event(self, event_id, scope) -> dict:
        target = self._target(event_id, scope)
        self._svc().events().delete(calendarId="primary", eventId=target).execute()
        return {"deleted": target}

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        resp = self._svc().freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]}).execute()
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
                for b in resp["calendars"]["primary"]["busy"]]
