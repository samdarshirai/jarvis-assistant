from datetime import datetime
from zoneinfo import ZoneInfo


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def parse_dt(value: str, tz: str) -> datetime:
    """ISO 8601 -> aware datetime. Naive input is read as local time in `tz`."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt
