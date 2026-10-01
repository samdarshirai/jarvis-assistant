import pytest
from jarvis.timeutil import parse_dt, now_local


def test_naive_gets_local_zone_with_dst():
    assert parse_dt("2026-07-01T09:00", "Europe/Berlin").isoformat() == "2026-07-01T09:00:00+02:00"
    assert parse_dt("2026-01-15T09:00", "Europe/Berlin").isoformat() == "2026-01-15T09:00:00+01:00"


def test_explicit_offset_preserved():
    assert parse_dt("2026-07-01T09:00:00Z", "Europe/Berlin").utcoffset().total_seconds() == 0


def test_garbage_raises_value_error():
    with pytest.raises(ValueError):
        parse_dt("next friday", "Europe/Berlin")


def test_now_local_is_aware():
    assert now_local("Europe/Berlin").tzinfo is not None
