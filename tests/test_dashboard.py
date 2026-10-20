import time
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.dashboard import DashboardService
from jarvis.google.auth import ReauthRequired
from jarvis.users import UserStore
from jarvis.voice.devices import Devices
from tests.test_appapi import Tagged, tenants

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


class Store:
    def __init__(self, **state):
        self.state = state

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value


def svc(cal=None, tasks=None, gmail=None, store=None, **kw):
    """The payload of a stand-in runtime holding these sources."""
    r = SimpleNamespace(calendar=cal or Cal(), tasks=tasks or Tasks(), gmail=gmail or Gmail(), notes=Notes(), tz=TZ,
                        proactive=store or Store(), user=SimpleNamespace(is_owner=True))
    return SimpleNamespace(payload=lambda now=None, coords=None: DashboardService(None, None, **kw).payload(r, now, coords))


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
    assert p["unread"] == {"count": 5, "more": True, "items": [{"id": f"m{i}", "from": f"p{i}@x.com", "subject": f"S{i}"} for i in range(5)]}
    assert gmail.call == ("is:unread in:inbox newer_than:7d", 6)
    assert p["notes"] == NOTES and p["reauth"] is False
    assert "at 09:00, Standup" in p["brief"] and "Pay rent" in p["brief"] and "unread" in p["brief"]


async def test_unread_without_more_and_missing_headers():
    p = await svc(gmail=Gmail([{"id": "m", "from": None, "subject": None}])).payload(NOW)
    assert p["unread"] == {"count": 1, "more": False, "items": [{"id": "m", "from": "", "subject": ""}]}


async def test_empty_everything():
    p = await svc(Cal([]), Tasks([]), Gmail([])).payload(NOW)
    assert p["events"] == [] and p["tasks"] == [] and p["unread"] == {"count": 0, "more": False, "items": []}
    assert "calendar is clear" in p["brief"]


async def test_a_failing_source_is_null_and_the_rest_survive():
    p = await svc(Cal(exc=RuntimeError("boom"))).payload(NOW)
    assert p["events"] is None and p["tasks"] and p["unread"] and p["notes"]
    assert "couldn't reach your calendar" in p["brief"] and p["reauth"] is False


async def test_reauth_sets_flag_and_nulls_the_source():
    p = await svc(gmail=Gmail(exc=ReauthRequired("x"))).payload(NOW)
    assert p["unread"] is None and p["reauth"] is True and p["events"]


async def test_a_hung_source_times_out_to_null():
    p = await svc(Cal(delay=0.5), source_timeout=0.05).payload(NOW)
    assert p["events"] is None and p["tasks"]


async def test_task_with_no_title_gets_placeholder():
    p = await svc(tasks=Tasks([{"id": "x", "title": None, "due": None, "status": "needsAction"}])).payload(NOW)
    assert p["tasks"][0]["title"] == "(no title)"


async def test_dashboard_scoped(pool, uids, monkeypatch):
    seen = []
    monkeypatch.setattr("jarvis.dashboard.weather.fetch", lambda city, tz, now, coords: seen.append((city, coords)) or None)
    devices, t = Devices(pool), tenants(pool)
    s = DashboardService(devices, t, weather_city="Server City")
    ha, hb = (f"Bearer {devices.create(u)[1]}" for u in uids)
    ra, rb = await s.authenticate(ha), await s.authenticate(hb)
    assert await s.authenticate("Bearer nope") is None
    for r, tag in ((ra, "A"), (rb, "B")):
        r.calendar = r.tasks = r.gmail = Tagged(tag)
        r.notes.create(f"{tag} note")
    rb.proactive.set_state("weather_city", "Pune")
    pa = await s.payload(ra, NOW, (52.5, 13.4))
    pb = await s.payload(rb, NOW)
    assert [n["title"] for n in pa["notes"]] == ["A note"] and [n["title"] for n in pb["notes"]] == ["B note"]
    assert {i["id"] for i in pa["unread"]["items"]} == {"A"} and {i["id"] for i in pb["unread"]["items"]} == {"B"}
    assert seen == [("", (52.5, 13.4)), ("Pune", None)]  # B never gets A's location; friends never the server city
    assert ra.proactive.get_state("last_location") == "52.5000,13.4000" and rb.proactive.get_state("last_location") is None


async def test_server_weather_city_is_the_owners_only(pool, uids, monkeypatch):
    seen = []
    monkeypatch.setattr("jarvis.dashboard.weather.fetch", lambda city, tz, now, coords: seen.append((city, coords)) or None)
    devices, t = Devices(pool), tenants(pool)
    s = DashboardService(devices, t, weather_city="Owner City")
    owner = UserStore(pool).add("owner@example.com", "Europe/Berlin", is_owner=True).id
    ro, rf = [await s.authenticate(f"Bearer {devices.create(u)[1]}") for u in (owner, uids[0])]
    for r in (ro, rf):
        r.calendar = r.tasks = r.gmail = Tagged("x")
    await s.payload(ro, NOW)
    pf = await s.payload(rf, NOW)
    assert seen == [("Owner City", None), ("", None)]  # a friend without a city or a phone fix gets no weather line
    assert "Owner City" not in str(pf)
