from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.google.slots import Window, free_slots

B = ZoneInfo("Europe/Berlin")


def dt(h, m=0, day=6):
    return datetime(2026, 10, day, h, m, tzinfo=B)


def test_skips_busy_and_returns_up_to_limit():
    out = free_slots([(dt(9), dt(11))], dt(9), dt(18), timedelta(hours=1))
    assert out[0] == (dt(11), dt(12))
    assert len(out) == 3


def test_windows_must_all_hold():
    # 15:00-16:00 Berlin = 18:30-19:30 IST
    w = [Window("Asia/Kolkata", time(18), time(21)), Window("Europe/Berlin", time(12), time(18))]
    out = free_slots([], dt(8), dt(20), timedelta(hours=1), w)
    assert out and all(s.hour >= 14 for s, _ in out)
    assert out[0][0] == dt(14, 30)


def test_empty_when_windows_do_not_intersect():
    # scenario 7 as written: IST evening and before 09:00 Berlin never overlap
    w = [Window("Asia/Kolkata", time(18), time(21)), Window("Europe/Berlin", time(0), time(9))]
    assert free_slots([], dt(0), dt(23), timedelta(hours=1), w) == []


def test_empty_range_returns_empty():
    assert free_slots([], dt(10), dt(10), timedelta(hours=1)) == []


def test_dst_change_day_is_handled():
    # 2026-10-25 Berlin falls back at 03:00; slots stay aligned to local half hours
    out = free_slots([], datetime(2026, 10, 25, 1, tzinfo=B), datetime(2026, 10, 25, 6, tzinfo=B),
                     timedelta(hours=1))
    assert out and all(s.minute in (0, 30) for s, _ in out)
