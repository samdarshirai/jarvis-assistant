from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from jarvis.google.calendar import CalendarClient
from jarvis.tools.calendar_tools import register_calendar_tools
from jarvis.tools.registry import Registry

TZ = "Europe/Berlin"
B = ZoneInfo(TZ)


def client(svc=None):
    svc = svc or MagicMock()
    return CalendarClient(lambda: svc, TZ), svc


def test_list_events_slims_results():
    c, svc = client()
    svc.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "e1", "summary": "Gym", "start": {"dateTime": "2026-10-06T07:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T08:00:00+02:00"}, "recurringEventId": "r1", "etag": "junk"}]}
    out = c.list_events(datetime(2026, 10, 6, tzinfo=B), datetime(2026, 10, 7, tzinfo=B))
    assert out == [{"id": "e1", "recurring_event_id": "r1", "summary": "Gym",
                    "start": "2026-10-06T07:00:00+02:00", "end": "2026-10-06T08:00:00+02:00",
                    "location": None}]
    kw = svc.events.return_value.list.call_args.kwargs
    assert kw["singleEvents"] is True and kw["calendarId"] == "primary"


def test_update_scope_this_targets_instance_all_targets_series():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "i1", "recurringEventId": "r1"}
    svc.events.return_value.patch.return_value.execute.return_value = {"id": "x"}
    c.update_event("i1", "this", summary="A")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "i1"
    c.update_event("i1", "all", summary="A")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "r1"


def test_delete_scope_all_on_non_recurring_uses_own_id():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "e9"}
    c.delete_event("e9", "all")
    assert svc.events.return_value.delete.call_args.kwargs["eventId"] == "e9"


def test_busy_parses_freebusy():
    c, svc = client()
    svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {
        "busy": [{"start": "2026-10-06T08:00:00Z", "end": "2026-10-06T09:00:00Z"}]}}}
    out = c.busy(datetime(2026, 10, 6, tzinfo=B), datetime(2026, 10, 7, tzinfo=B))
    assert out[0][0].hour == 8 and out[0][0].utcoffset().total_seconds() == 0


def make_tools():
    c, svc = client()
    r = Registry()
    register_calendar_tools(r, c, TZ)
    return r, svc


def test_confirm_tags():
    r, _ = make_tools()
    assert {t.name for t in r.for_domain("calendar") if not t.needs_confirm} == {"list_events", "find_free_slots"}
    for n in ("create_event", "update_event", "delete_event"):
        assert r.needs_confirm(n)


def test_create_event_rejects_end_before_start_without_calling_google():
    r, svc = make_tools()
    with pytest.raises(ValueError):
        r.get("create_event").fn(summary="x", start="2026-10-06T10:00", end="2026-10-06T09:00")
    svc.events.return_value.insert.assert_not_called()


def test_create_event_rejects_unparsable_datetime():
    r, svc = make_tools()
    with pytest.raises(ValueError):
        r.get("create_event").fn(summary="x", start="tomorrow", end="2026-10-06T09:00")
    svc.events.assert_not_called()


def test_find_free_slots_no_intersection_returns_empty_list():
    r, svc = make_tools()
    svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {"busy": []}}}
    out = r.get("find_free_slots").fn(
        duration_minutes=60, range_start="2026-10-06T00:00", range_end="2026-10-06T23:00",
        windows=[{"tz": "Asia/Kolkata", "start": "18:00", "end": "21:00"},
                 {"tz": "Europe/Berlin", "start": "00:00", "end": "09:00"}])
    assert out == []


def test_update_tool_scope_all_rejects_start_end():
    r, svc = make_tools()
    with pytest.raises(ValueError):
        r.get("update_event").fn(event_id="i1", scope="all", start="2026-10-06T10:00")
    svc.events.return_value.patch.assert_not_called()


def test_update_tool_scope_all_summary_only_patches_series():
    r, svc = make_tools()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "i1", "recurringEventId": "r1"}
    svc.events.return_value.patch.return_value.execute.return_value = {"id": "r1"}
    r.get("update_event").fn(event_id="i1", scope="all", summary="A")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "r1"


def test_update_tool_scope_this_with_start_works():
    r, svc = make_tools()
    svc.events.return_value.patch.return_value.execute.return_value = {"id": "i1"}
    r.get("update_event").fn(event_id="i1", scope="this", start="2026-10-06T10:00")
    assert svc.events.return_value.patch.call_args.kwargs["eventId"] == "i1"
