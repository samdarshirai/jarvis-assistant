from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from jarvis.tools.calendar_tools import CreateEventArgs, UpdateEventArgs, register_calendar_tools
from jarvis.tools.registry import Registry

TZ = "Europe/Berlin"
B = ZoneInfo(TZ)


def conflict(summary="Standup", s="2026-10-06T09:00:00+02:00", e="2026-10-06T09:30:00+02:00"):
    return {"id": "x", "summary": summary, "start": s, "end": e, "location": None, "all_day": False,
            "declined": False, "busy": True, "source_message": None}


def setup(sent_to="default"):
    client = MagicMock()
    client.conflicts.return_value = []
    client.busy.return_value = []
    r = Registry()
    register_calendar_tools(r, client, TZ, (lambda a: True) if sent_to == "default" else sent_to)
    return r, client


CREATE = {"summary": "Lunch", "start": "2026-10-06T12:00:00", "end": "2026-10-06T13:00:00"}
BASE = "Create 'Lunch' Tue 2026-10-06 12:00-13:00"


def test_plain_create_card_is_unchanged_and_runs_the_conflict_check():
    r, client = setup()
    assert r.get("create_event").describe(CREATE) == BASE
    client.conflicts.assert_called_once_with(datetime(2026, 10, 6, 12, tzinfo=B), datetime(2026, 10, 6, 13, tzinfo=B), None)


def test_card_lists_reminders_and_invitees():
    r, _ = setup()
    text = r.get("create_event").describe({**CREATE, "reminders": [1440, 60, 0, 90],
                                           "attendees": ["raj@x.com", "mia@y.org"]})
    assert text == (BASE + "\nReminders: 1 day before, 1 hour before, at the start, 90 minutes before"
                    "\nInvites emailed to: raj@x.com, mia@y.org")


def test_card_flags_invitees_you_never_emailed_and_checks_failures():
    r, _ = setup(sent_to=lambda a: a == "raj@x.com")
    text = r.get("create_event").describe({**CREATE, "attendees": ["raj@x.com", "stranger@z.io"]})
    assert text.endswith("Invites emailed to: raj@x.com, stranger@z.io (never emailed by you)")

    def boom(a):
        raise RuntimeError("gmail down")

    r, _ = setup(sent_to=boom)
    assert "raj@x.com (could not check)" in r.get("create_event").describe({**CREATE, "attendees": ["raj@x.com"]})
    r, _ = setup(sent_to=None)
    assert r.get("create_event").describe({**CREATE, "attendees": ["raj@x.com"]}).endswith("Invites emailed to: raj@x.com")


def test_card_warns_about_conflicts_and_offers_three_free_slots():
    r, client = setup()
    client.conflicts.return_value = [conflict("Standup", "2026-10-06T12:00:00+02:00", "2026-10-06T12:30:00+02:00")]
    client.busy.return_value = [(datetime(2026, 10, 6, 12, tzinfo=B), datetime(2026, 10, 6, 12, 30, tzinfo=B))]
    text = r.get("create_event").describe(CREATE)
    assert text == (BASE + "\nWarning: conflicts with 'Standup' Tue 2026-10-06 12:00-12:30."
                    " Free instead: Tue 2026-10-06 12:30-13:30; Tue 2026-10-06 13:30-14:30; Tue 2026-10-06 14:30-15:30.")


def test_card_names_at_most_three_conflicts_and_handles_a_full_week():
    r, client = setup()
    client.conflicts.return_value = [conflict(f"E{i}") for i in range(5)]
    client.busy.return_value = [(datetime(2026, 10, 6, 0, tzinfo=B), datetime(2026, 10, 14, 0, tzinfo=B))]
    text = r.get("create_event").describe(CREATE)
    assert "'E0'" in text and "'E2'" in text and "'E3'" not in text and "and 2 more" in text
    assert text.endswith("No free slot found in the next 7 days between 08:00 and 20:00.")


def test_card_says_when_the_conflict_check_failed():
    r, client = setup()
    client.conflicts.side_effect = RuntimeError("google down")
    assert r.get("create_event").describe(CREATE).endswith("\n(could not check for conflicts)")


def ev(start="2026-10-06T09:00:00+02:00", end="2026-10-06T10:00:00+02:00"):
    return {"id": "e1", "summary": "Gym", "start": start, "end": end, "location": None, "recurring_event_id": None}


def test_update_checks_the_new_window_with_the_existing_duration_and_excludes_itself():
    r, client = setup()
    client.get_event.return_value = ev()
    r.get("update_event").describe({"event_id": "e1", "scope": "this", "start": "2026-10-06T15:00:00"})
    client.conflicts.assert_called_once_with(datetime(2026, 10, 6, 15, tzinfo=B), datetime(2026, 10, 6, 16, tzinfo=B), "e1")


def test_update_with_only_a_new_end_keeps_the_existing_start():
    r, client = setup()
    client.get_event.return_value = ev()
    r.get("update_event").describe({"event_id": "e1", "scope": "this", "end": "2026-10-06T11:00:00"})
    s, e, _ = client.conflicts.call_args.args
    assert s == datetime(2026, 10, 6, 9, tzinfo=B) and e == datetime(2026, 10, 6, 11, tzinfo=B)


def test_update_without_time_change_or_for_a_series_skips_the_conflict_check():
    r, client = setup()
    client.get_event.return_value = ev()
    r.get("update_event").describe({"event_id": "e1", "scope": "this", "summary": "New"})
    r.get("update_event").describe({"event_id": "e1", "scope": "all", "summary": "New"})
    client.conflicts.assert_not_called()


def test_update_card_lists_new_invitees_and_reminders():
    r, client = setup()
    client.get_event.return_value = ev()
    text = r.get("update_event").describe({"event_id": "e1", "scope": "this", "add_attendees": ["raj@x.com"], "reminders": [60]})
    assert "Reminders: 1 hour before" in text and "Adds invitees (they are emailed): raj@x.com" in text


def test_create_passes_reminders_and_cleaned_attendees_and_omits_unset_extras():
    r, client = setup()
    r.get("create_event").fn(**CREATE, reminders=[60], attendees=["Raj@X.com", " mia@y.org "])
    kw = client.create_event.call_args.kwargs
    assert kw["reminders"] == [60] and kw["attendees"] == ["Raj@X.com", "mia@y.org"]
    client.create_event.reset_mock()
    r.get("create_event").fn(**CREATE)
    assert set(client.create_event.call_args.kwargs) == {"recurrence"}


@pytest.mark.parametrize("attendees", [["not an address"], ["a@b.com"] * 11, ["a@b.com\nbcc:evil@x.com"]])
def test_bad_attendees_are_rejected_before_any_google_call(attendees):
    r, client = setup()
    with pytest.raises(ValueError):
        r.get("create_event").fn(**CREATE, attendees=attendees)
    client.create_event.assert_not_called()


def test_update_passes_add_attendees_and_reminders():
    r, client = setup()
    r.get("update_event").fn(event_id="e1", scope="this", add_attendees=["raj@x.com"], reminders=[30])
    kw = client.update_event.call_args.kwargs
    assert kw["add_attendees"] == ["raj@x.com"] and kw["reminders"] == [30]


def test_argument_limits():
    for bad in ([-1], [40321], [1, 2, 3, 4, 5, 6]):
        with pytest.raises(ValidationError):
            CreateEventArgs(**CREATE, reminders=bad)
    with pytest.raises(ValidationError):
        CreateEventArgs(**CREATE, attendees=["a@b.com"] * 11)
    with pytest.raises(ValidationError):
        UpdateEventArgs(event_id="e", scope="this", reminders=[-5])
    CreateEventArgs(**CREATE, reminders=[0, 40320], attendees=["a@b.com"] * 10)


def test_writes_stay_confirm_gated():
    r, _ = setup()
    for name in ("create_event", "update_event", "delete_event"):
        assert r.needs_confirm(name) is True
