import time
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.dashboard import DashboardService
from jarvis.google.auth import ReauthRequired

TZ = "Europe/Berlin"
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=ZoneInfo(TZ))
EVENTS = [
    {"id": "1", "summary": "Standup", "start": "2026-10-05T09:00:00+02:00", "end": "2026-10-05T09:15:00+02:00", "location": "Office"},
    {"id": "2", "summary": None, "start": "2026-10-05", "end": "2026-10-06", "location": None},
]
TASKS = [
    {"id": "t3", "title": "No date", "due": None, "status": "needsAction"},
    {"id": "t2", "title": "Due today", "due": "2026-10-05", "status": "needsAction"},
    {"id": "t1", "title": "Pay rent", "due": "2026-10-03", "status": "needsAction"},
]
MAIL = [{"id": f"m{i}", "from": f"p{i}@x.com", "subject": f"S{i}"} for i in range(7)]
NOTES = [{"id": 1, "title": "Gym plan", "updated_at": "2026-10-05T08:00:00+00:00"}]


class Cal:
    def __init__(self, events=EVENTS, exc=None, delay=0.0):
        self.events, self.exc, self.delay, self.window = events, exc, delay, None

    def list_events(self, start, end, query=None, limit=50):
        self.window = (start, end)
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.events


class Tasks:
    def __init__(self, tasks=TASKS):
        self.tasks = tasks

    def list_tasks(self, include_completed=False, tasklist="@default"):
        return self.tasks


class Gmail:
    def __init__(self, mail=MAIL, exc=None):
        self.mail, self.exc, self.call = mail, exc, None

    def search_emails(self, query, limit=10):
        self.call = (query, limit)
        if self.exc:
            raise self.exc
        return self.mail[:limit]


class Notes:
    def recent(self, limit=20):
        return NOTES[:limit]


class Devices:
    def verify(self, token):
        return 1 if token == "good" else None


def svc(cal=None, tasks=None, gmail=None, **kw):
    return DashboardService(Devices(), cal or Cal(), tasks or Tasks(), gmail or Gmail(), Notes(), TZ, **kw)


async def test_payload_shapes_every_section():
    cal, gmail = Cal(), Gmail()
    p = await svc(cal, gmail=gmail).payload(NOW)
    assert p["events"] == [
        {"summary": "Standup", "start": "2026-10-05T09:00:00+02:00", "end": "2026-10-05T09:15:00+02:00", "location": "Office", "all_day": False},
        {"summary": "(no title)", "start": "2026-10-05", "end": "2026-10-06", "location": None, "all_day": True},
    ]
    start, end = cal.window
    assert (start.hour, start.minute) == (0, 0) and (end - start).days == 1 and start.date() == NOW.date()
    assert [(t["title"], t["overdue"]) for t in p["tasks"]] == [("Pay rent", True), ("Due today", False), ("No date", False)]
    assert p["unread"] == {"count": 5, "more": True, "items": [{"from": f"p{i}@x.com", "subject": f"S{i}"} for i in range(5)]}
    assert gmail.call == ("is:unread in:inbox newer_than:7d", 6)
    assert p["notes"] == NOTES and p["reauth"] is False
    assert "09:00 Standup" in p["brief"] and "Overdue tasks: Pay rent." in p["brief"] and "Unread email:" in p["brief"]


async def test_unread_without_more_and_missing_headers():
    p = await svc(gmail=Gmail([{"id": "m", "from": None, "subject": None}])).payload(NOW)
    assert p["unread"] == {"count": 1, "more": False, "items": [{"from": "", "subject": ""}]}


async def test_empty_everything():
    p = await svc(Cal([]), Tasks([]), Gmail([])).payload(NOW)
    assert p["events"] == [] and p["tasks"] == [] and p["unread"] == {"count": 0, "more": False, "items": []}
    assert "no events" in p["brief"]


async def test_a_failing_source_is_null_and_the_rest_survive():
    p = await svc(Cal(exc=RuntimeError("boom"))).payload(NOW)
    assert p["events"] is None and p["tasks"] and p["unread"] and p["notes"]
    assert "Calendar: unavailable" in p["brief"] and p["reauth"] is False


async def test_reauth_sets_flag_and_nulls_the_source():
    p = await svc(gmail=Gmail(exc=ReauthRequired("x"))).payload(NOW)
    assert p["unread"] is None and p["reauth"] is True and p["events"]


async def test_a_hung_source_times_out_to_null():
    p = await svc(Cal(delay=0.5), source_timeout=0.05).payload(NOW)
    assert p["events"] is None and p["tasks"]


async def test_authorized_needs_a_known_bearer_token():
    s = svc()
    assert await s.authorized("Bearer good") is True
    assert await s.authorized("bearer good") is True
    for bad in ("Bearer nope", "Bearer ", "good", ""):
        assert await s.authorized(bad) is False


async def test_task_with_no_title_gets_placeholder():
    p = await svc(tasks=Tasks([{"id": "x", "title": None, "due": None, "status": "needsAction"}])).payload(NOW)
    assert p["tasks"][0]["title"] == "(no title)"
