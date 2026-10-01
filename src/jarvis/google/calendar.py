from datetime import datetime
from typing import Any, Callable


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
