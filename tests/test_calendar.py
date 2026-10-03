from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.errors import HttpError

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


def test_get_event_slims():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {
        "id": "e1", "summary": "Gym", "start": {"dateTime": "2026-10-06T07:00:00+02:00"},
        "end": {"dateTime": "2026-10-06T08:00:00+02:00"}, "etag": "junk"}
    assert c.get_event("e1")["summary"] == "Gym"
    assert svc.events.return_value.get.call_args.kwargs == {"calendarId": "primary", "eventId": "e1"}


def described_registry():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {
        "id": "e1", "summary": "Gym", "start": {"dateTime": "2026-10-06T07:00:00+02:00"},
        "end": {"dateTime": "2026-10-06T08:00:00+02:00"}}
    r = Registry()
    register_calendar_tools(r, c, TZ)
    return r, svc


def test_delete_describe_names_event_and_series():
    r, svc = described_registry()
    s = r.get("delete_event").describe({"event_id": "e1", "scope": "all"})
    assert "Delete 'Gym' (Tue 2026-10-06 07:00-08:00)" in s and "whole recurring series" in s
    assert "series" not in r.get("delete_event").describe({"event_id": "e1", "scope": "this"})
    assert svc.events.return_value.get.call_count == 2


def test_update_describe_lists_changed_fields():
    r, svc = described_registry()
    s = r.get("update_event").describe({"event_id": "e1", "scope": "this", "summary": "Run",
                                        "start": "2026-10-07T07:00:00", "end": "2026-10-07T08:00:00"})
    assert "Change 'Gym' (Tue 2026-10-06 07:00-08:00)" in s
    assert "title" in s and "'Run'" in s and "Wed 2026-10-07 07:00" in s
    assert svc.events.return_value.get.call_count == 1


def test_create_event_describe_is_offline():
    r, svc = described_registry()
    s = r.get("create_event").describe({"summary": "Gym", "start": "2026-10-06T07:00:00", "end": "2026-10-06T08:00:00"})
    assert s == "Create 'Gym' Tue 2026-10-06 07:00-08:00"
    # the card now reads the calendar for conflicts, but must never write
    for w in ("insert", "update", "patch", "delete"):
        getattr(svc.events.return_value, w).assert_not_called()


def test_list_for_proactive_maps_flags_and_drops_cancelled():
    c, svc = client()
    svc.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "a", "summary": "Standup", "start": {"dateTime": "2026-10-06T09:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T09:15:00+02:00"}, "location": "Office"},
        {"id": "b", "start": {"date": "2026-10-06"}, "end": {"date": "2026-10-07"}, "transparency": "transparent"},
        {"id": "c", "summary": "Declined", "start": {"dateTime": "2026-10-06T10:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T11:00:00+02:00"},
         "attendees": [{"email": "x@y.z"}, {"self": True, "responseStatus": "declined"}]},
        {"id": "d", "status": "cancelled", "start": {"dateTime": "2026-10-06T12:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T13:00:00+02:00"}},
        {"id": "e", "summary": "Mine", "start": {"dateTime": "2026-10-06T14:00:00+02:00"},
         "end": {"dateTime": "2026-10-06T15:00:00+02:00"},
         "extendedProperties": {"private": {"jarvisMsgId": "m1"}}},
    ]}
    out = {e["id"]: e for e in c.list_for_proactive(datetime(2026, 10, 6, tzinfo=B), datetime(2026, 10, 8, tzinfo=B))}
    assert set(out) == {"a", "b", "c", "e"}
    assert out["a"] == {"id": "a", "summary": "Standup", "start": "2026-10-06T09:00:00+02:00",
                        "end": "2026-10-06T09:15:00+02:00", "location": "Office", "all_day": False,
                        "declined": False, "busy": True, "source_message": None}
    assert out["b"]["all_day"] is True and out["b"]["busy"] is False and out["b"]["summary"] == ""
    assert out["c"]["declined"] is True
    assert out["e"]["source_message"] == "m1"
    kw = svc.events.return_value.list.call_args.kwargs
    assert kw["singleEvents"] is True and kw["calendarId"] == "primary"


def test_create_auto_event_sends_id_reminders_and_message_property():
    c, svc = client()
    svc.events.return_value.insert.return_value.execute.return_value = {"id": "abc", "summary": "Flight"}
    out = c.create_auto_event("abc", "Flight", datetime(2026, 10, 9, 8, 10, tzinfo=B), datetime(2026, 10, 9, 10, 10, tzinfo=B),
                              "FRA", [1440, 180], "m1", "Added by Jarvis from an email: Your flight")
    body = svc.events.return_value.insert.call_args.kwargs["body"]
    assert out["id"] == "abc"
    assert body["id"] == "abc" and body["location"] == "FRA"
    assert body["description"] == "Added by Jarvis from an email: Your flight"
    assert body["reminders"] == {"useDefault": False, "overrides": [
        {"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 180}]}
    assert body["extendedProperties"] == {"private": {"jarvisMsgId": "m1"}}
    assert body["start"]["timeZone"] == TZ and "attendees" not in body


def test_create_auto_event_omits_empty_location_and_treats_409_as_already_there():
    c, svc = client()
    insert = svc.events.return_value.insert.return_value.execute
    insert.return_value = {"id": "abc"}
    c.create_auto_event("abc", "X", datetime(2026, 10, 9, 8, tzinfo=B), datetime(2026, 10, 9, 9, tzinfo=B), None, [60], "m", "d")
    assert "location" not in svc.events.return_value.insert.call_args.kwargs["body"]
    insert.side_effect = HttpError(httplib2.Response({"status": "409"}), b"")
    assert c.create_auto_event("abc", "X", datetime(2026, 10, 9, 8, tzinfo=B), datetime(2026, 10, 9, 9, tzinfo=B), None, [60], "m", "d") is None
    insert.side_effect = HttpError(httplib2.Response({"status": "500"}), b"")
    with pytest.raises(HttpError):
        c.create_auto_event("abc", "X", datetime(2026, 10, 9, 8, tzinfo=B), datetime(2026, 10, 9, 9, tzinfo=B), None, [60], "m", "d")


def test_create_event_with_reminders_and_attendees_sends_updates_only_with_attendees():
    c, svc = client()
    insert = svc.events.return_value.insert
    insert.return_value.execute.return_value = {"id": "e1", "summary": "Lunch"}
    s, e = datetime(2026, 10, 9, 12, tzinfo=B), datetime(2026, 10, 9, 13, tzinfo=B)
    c.create_event("Lunch", s, e, reminders=[1440, 60], attendees=["raj@x.com", "mia@y.org"])
    kw = insert.call_args.kwargs
    assert kw["sendUpdates"] == "all"
    assert kw["body"]["attendees"] == [{"email": "raj@x.com"}, {"email": "mia@y.org"}]
    assert kw["body"]["reminders"] == {"useDefault": False, "overrides": [
        {"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}]}
    c.create_event("Solo", s, e)
    kw = insert.call_args.kwargs
    assert "sendUpdates" not in kw and "attendees" not in kw["body"] and "reminders" not in kw["body"]


def test_update_event_adds_attendees_without_dropping_existing_and_notifies():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {
        "id": "i1", "attendees": [{"email": "Raj@X.com", "responseStatus": "accepted"}]}
    patch = svc.events.return_value.patch
    patch.return_value.execute.return_value = {"id": "i1"}
    c.update_event("i1", "this", add_attendees=["raj@x.com", "mia@y.org"], reminders=[30])
    kw = patch.call_args.kwargs
    assert kw["sendUpdates"] == "all"
    assert kw["body"]["attendees"] == [{"email": "Raj@X.com", "responseStatus": "accepted"}, {"email": "mia@y.org"}]
    assert kw["body"]["reminders"]["overrides"] == [{"method": "popup", "minutes": 30}]


def test_update_event_with_only_already_invited_people_does_not_notify():
    c, svc = client()
    svc.events.return_value.get.return_value.execute.return_value = {"id": "i1", "attendees": [{"email": "raj@x.com"}]}
    patch = svc.events.return_value.patch
    patch.return_value.execute.return_value = {"id": "i1"}
    c.update_event("i1", "this", summary="New", add_attendees=["RAJ@x.com"])
    kw = patch.call_args.kwargs
    assert "sendUpdates" not in kw and "attendees" not in kw["body"] and kw["body"] == {"summary": "New"}


def conflict_items():
    def item(id, s, e, **kw):
        base = {"id": id, "summary": id, "start": s, "end": e, "location": None, "all_day": False,
                "declined": False, "busy": True, "source_message": None}
        return {**base, **kw}
    return [
        item("overlap", "2026-10-06T09:30:00+02:00", "2026-10-06T10:30:00+02:00"),
        item("touching", "2026-10-06T10:00:00+02:00", "2026-10-06T11:00:00+02:00"),
        item("before", "2026-10-06T08:00:00+02:00", "2026-10-06T09:00:00+02:00"),
        item("allday", "2026-10-06", "2026-10-07", all_day=True),
        item("declined", "2026-10-06T09:15:00+02:00", "2026-10-06T09:45:00+02:00", declined=True),
        item("free", "2026-10-06T09:15:00+02:00", "2026-10-06T09:45:00+02:00", busy=False),
        item("self", "2026-10-06T09:10:00+02:00", "2026-10-06T09:20:00+02:00"),
    ]


def test_conflicts_are_strict_overlaps_of_busy_timed_events_excluding_the_moved_one():
    c, _ = client()
    c.list_for_proactive = lambda s, e: conflict_items()
    start, end = datetime(2026, 10, 6, 9, 0, tzinfo=B), datetime(2026, 10, 6, 10, 0, tzinfo=B)
    assert [x["id"] for x in c.conflicts(start, end, exclude_id="self")] == ["overlap"]
    assert [x["id"] for x in c.conflicts(start, end)] == ["overlap", "self"]
