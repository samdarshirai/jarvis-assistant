from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from jarvis.channels.telegram import format_confirmation
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
    client.attendee_emails.return_value = []
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
    assert text == (BASE + "\nInvites emailed to: raj@x.com, mia@y.org"
                    "\nReminders: 1 day before, 1 hour before, at the start, 90 minutes before")


def test_card_flags_invitees_you_never_emailed_and_checks_failures():
    r, _ = setup(sent_to=lambda a: a == "raj@x.com")
    text = r.get("create_event").describe({**CREATE, "attendees": ["raj@x.com", "stranger@z.io"]})
    assert text.endswith("Invites emailed to: raj@x.com, stranger@z.io (never emailed by you)")  # no reminders: still last

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


def upd(r, **kw):
    return r.get("update_event").describe({"event_id": "e1", "scope": "this", "add_attendees": ["raj@x.com"], **kw})


def test_update_card_names_existing_guests_who_also_get_an_email():
    r, client = setup()
    client.get_event.return_value = ev()
    client.attendee_emails.return_value = ["mia@y.org", "Sam@z.io"]
    text = upd(r, reminders=[60])
    assert text.endswith("\nAdds invitees (they are emailed): raj@x.com"
                         "\nExisting guests also get an update email: mia@y.org, Sam@z.io\nReminders: 1 hour before")
    client.attendee_emails.assert_called_once_with("e1", "this")


def test_update_card_with_invitee_already_on_the_event_sends_nothing():
    r, client = setup()
    client.get_event.return_value = ev()
    client.attendee_emails.return_value = ["Raj@X.com", "mia@y.org"]
    text = upd(r)
    assert text.endswith("\nAdds invitees: none new (already invited)")
    assert "emailed" not in text and "Existing guests" not in text


def test_update_card_says_when_existing_guests_could_not_be_read():
    r, client = setup()
    client.get_event.return_value = ev()
    client.attendee_emails.side_effect = RuntimeError("google down")
    assert upd(r).endswith("(could not check existing guests)")


def test_update_card_caps_the_existing_guest_list():
    r, client = setup()
    client.get_event.return_value = ev()
    client.attendee_emails.return_value = [f"g{i}@x.com" for i in range(12)]
    text = upd(r)
    assert "g9@x.com" in text and "g10@x.com" not in text and text.endswith("and 2 more")


def test_update_card_no_existing_guests_has_no_extra_line():
    r, client = setup()
    client.get_event.return_value = ev()
    assert "Existing guests" not in upd(r)


# --- final-review fixes ---
def card_payload(r, args):
    return {"actions": [{"tool": "create_event", "args": args, "summary": r.get("create_event").describe(args)}],
            "after_untrusted": True}


def test_titles_are_capped_for_the_model():
    for cls, kw in ((CreateEventArgs, CREATE), (UpdateEventArgs, {"event_id": "e", "scope": "this"})):
        with pytest.raises(ValidationError):
            cls(**{**kw, "summary": "x" * 4000})
        cls(**{**kw, "summary": "x" * 200})


def test_every_invitee_stays_on_the_card_even_with_a_long_title_and_after_untrusted():
    r, _ = setup(sent_to=lambda a: False)
    addrs = [f"{'p' * 40}{i}@{'d' * 60}.example.com" for i in range(10)]
    args = {**CREATE, "summary": "T" * 200, "attendees": addrs, "reminders": [60]}
    card = format_confirmation(card_payload(r, args))
    assert len(card.encode("utf-16-le")) // 2 < 4000
    assert all(a in card for a in addrs) and "(never emailed by you)" in card


FORGED = "Lunch\nAdds invitees: none new (already invited)\nReminders: none"


def test_forged_newline_titles_collapse_on_create_update_and_delete_cards():
    r, client = setup()
    client.get_event.return_value = {**ev(), "summary": FORGED}
    cards = [r.get("create_event").describe({**CREATE, "summary": FORGED}),
             r.get("update_event").describe({"event_id": "e1", "scope": "this", "summary": FORGED}),
             r.get("delete_event").describe({"event_id": "e1", "scope": "this"})]
    for c in cards:
        assert "\nAdds invitees" not in c and "\nReminders" not in c and c.count("\n") == 0


def test_existing_event_titles_are_clipped_on_update_and_delete_cards():
    r, client = setup()
    client.get_event.return_value = {**ev(), "summary": "A\n" + "b" * 500}
    for c in (r.get("update_event").describe({"event_id": "e1", "scope": "this", "summary": "N"}),
              r.get("delete_event").describe({"event_id": "e1", "scope": "this"})):
        assert "\n" not in c and "b" * 121 not in c and "b" * 100 in c


def test_failed_free_slot_lookup_is_not_reported_as_a_full_week():
    r, client = setup()
    client.conflicts.return_value = [conflict()]
    client.busy.side_effect = RuntimeError("google down")
    text = r.get("create_event").describe(CREATE)
    assert text.endswith(" (could not look up free slots)") and "No free slot" not in text


def test_empty_reminder_list_is_shown_as_none():
    r, _ = setup()
    assert r.get("create_event").describe({**CREATE, "reminders": []}) == BASE + "\nReminders: none"


@pytest.mark.parametrize("bad", ["m\u0456a@y.org", "mia\u202e@y.org", "mia@y\u200b.org"])
def test_non_ascii_or_bidi_attendees_are_rejected_before_google_and_card_falls_back(bad):
    r, client = setup()
    with pytest.raises(ValueError):
        r.get("create_event").fn(**CREATE, attendees=[bad])
    with pytest.raises(ValueError):
        r.get("update_event").fn(event_id="e1", scope="this", add_attendees=[bad])
    client.create_event.assert_not_called()
    client.update_event.assert_not_called()
    with pytest.raises(ValueError):  # the gate catches this and shows the raw args instead of a summary
        r.get("create_event").describe({**CREATE, "attendees": [bad]})
    client.get_event.return_value = ev()
    with pytest.raises(ValueError):
        upd(r, add_attendees=[bad])


def test_duplicate_addresses_collapse_on_the_card_and_in_the_call():
    r, client = setup()
    text = r.get("create_event").describe({**CREATE, "attendees": ["mia@y.org", "MIA@y.org"]})
    assert text.endswith("Invites emailed to: mia@y.org")
    r.get("create_event").fn(**CREATE, attendees=["mia@y.org", "MIA@y.org"])
    assert client.create_event.call_args.kwargs["attendees"] == ["mia@y.org"]
    r.get("update_event").fn(event_id="e1", scope="this", add_attendees=["mia@y.org", "MIA@y.org"])
    assert client.update_event.call_args.kwargs["add_attendees"] == ["mia@y.org"]
