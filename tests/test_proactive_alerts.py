from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis.proactive.alerts import conflict_key, find_conflicts, leave_key, leave_now_due, sweep
from tests.fakes import FakeNotifier, FakeProactiveStore

TZ = "Europe/Berlin"
B = ZoneInfo(TZ)
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=B)


def at(h, m=0):
    return datetime(2026, 10, 6, h, m, tzinfo=B)


def ev(id, start, end, **kw):
    return {"id": id, "summary": id, "start": start.isoformat(), "end": end.isoformat(), "location": None,
            "all_day": False, "declined": False, "busy": True, "source_message": None, **kw}


def ids(pairs):
    return [(a["id"], b["id"]) for a, b in pairs]


def test_overlap_is_a_conflict_but_back_to_back_is_not():
    assert ids(find_conflicts([ev("a", at(9), at(10)), ev("b", at(9, 30), at(11))], TZ)) == [("a", "b")]
    assert find_conflicts([ev("a", at(9), at(10)), ev("b", at(10), at(11))], TZ) == []


def test_three_way_overlap_reports_every_pair():
    evs = [ev("a", at(9), at(12)), ev("b", at(10), at(11)), ev("c", at(10, 30), at(11, 30))]
    assert sorted(ids(find_conflicts(evs, TZ))) == [("a", "b"), ("a", "c"), ("b", "c")]


def test_all_day_declined_and_free_events_never_conflict():
    base = ev("a", at(9), at(11))
    for other in (ev("b", at(10), at(12), all_day=True), ev("b", at(10), at(12), declined=True),
                  ev("b", at(10), at(12), busy=False)):
        assert find_conflicts([base, other], TZ) == []


def test_all_day_event_with_date_strings_does_not_crash():
    day = {"id": "d", "summary": "Holiday", "start": "2026-10-06", "end": "2026-10-07", "location": "Home",
           "all_day": True, "declined": False, "busy": False, "source_message": None}
    assert find_conflicts([day, ev("a", at(9), at(10))], TZ) == []
    assert leave_now_due([day], NOW, 30, TZ) == []


def test_leave_now_window_edges():
    def due(start, **kw):
        return ids_of(leave_now_due([ev("x", start, start + timedelta(hours=1), location="Cafe", **kw)], NOW, 30, TZ))

    def ids_of(evs):
        return [e["id"] for e in evs]

    assert due(NOW + timedelta(minutes=30)) == ["x"]          # exactly the lead time
    assert due(NOW + timedelta(minutes=31)) == []             # too early
    assert due(NOW) == []                                     # already started
    assert due(NOW + timedelta(minutes=10), declined=True) == []
    no_loc = ev("y", NOW + timedelta(minutes=10), NOW + timedelta(hours=1))
    assert leave_now_due([no_loc], NOW, 30, TZ) == []         # no location, nowhere to leave for


class FakeCalendar:
    def __init__(self, events):
        self.events, self.windows = events, []

    def list_for_proactive(self, start, end):
        self.windows.append((start, end))
        return self.events


async def test_sweep_alerts_once_across_repeated_sweeps_and_asks_for_48_hours():
    cal = FakeCalendar([ev("a", at(9), at(10)), ev("b", at(9, 30), at(11)),
                        ev("trip", at(8, 20), at(9, 0), location="Station")])
    store, n = FakeProactiveStore(), FakeNotifier()
    await sweep(cal, store, n, TZ, 30, now=NOW)
    await sweep(cal, store, n, TZ, 30, now=NOW + timedelta(minutes=5))
    kinds = [(k, t.split(":")[0]) for k, t, _ in n.calls]
    assert kinds == [("telegram", "Calendar conflict"), ("telegram", "Time to leave"), ("push", "Time to leave")]
    assert cal.windows[0] == (NOW, NOW + timedelta(hours=48))


async def test_a_moved_event_alerts_again():
    store, n = FakeProactiveStore(), FakeNotifier()
    await sweep(FakeCalendar([ev("a", at(9), at(10)), ev("b", at(9, 30), at(11))]), store, n, TZ, 30, now=NOW)
    await sweep(FakeCalendar([ev("a", at(9), at(10)), ev("b", at(9, 45), at(11))]), store, n, TZ, 30, now=NOW)
    assert len([c for c in n.calls if c[0] == "telegram"]) == 2


def test_keys_are_order_independent_and_include_the_start():
    a, b = ev("a", at(9), at(10)), ev("b", at(9, 30), at(11))
    assert conflict_key(a, b) == conflict_key(b, a)
    assert leave_key(a) != leave_key(ev("a", at(11), at(12)))
