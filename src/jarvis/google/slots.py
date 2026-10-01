from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    """Slot must lie wholly inside [start, end] local time in `tz`, on one calendar day there."""
    tz: str
    start: time
    end: time


def _ceil(t: datetime, step: timedelta) -> datetime:
    """Ceil an aware instant to the step grid of its own local wall clock (offset taken at t); result is UTC."""
    off = t.utcoffset()
    local = t.astimezone(timezone.utc) + off  # UTC-aware value shifted to local wall-clock seconds
    base = local.replace(minute=0, second=0, microsecond=0)
    n = -(-(local - base) // step)
    return base + n * step - off


def _in_window(start: datetime, end: datetime, w: Window) -> bool:
    z = ZoneInfo(w.tz)
    s, e = start.astimezone(z), end.astimezone(z)
    return s.date() == e.date() and w.start <= s.time() and e.time() <= w.end


def free_slots(busy, range_start, range_end, duration, windows=(), step=timedelta(minutes=30), limit=3):
    # All stepping is in UTC (absolute time); local zones only matter in _in_window and the output.
    zone = range_start.tzinfo
    utc = timezone.utc
    busy = [(a.astimezone(utc), b.astimezone(utc)) for a, b in busy]
    end_all = range_end.astimezone(utc)
    out = []
    t = _ceil(range_start, step)
    while t + duration <= end_all and len(out) < limit:
        end = t + duration
        clash = any(b0 < end and t < b1 for b0, b1 in busy)
        if not clash and all(_in_window(t, end, w) for w in windows):
            out.append((t.astimezone(zone), end.astimezone(zone)))
            t = end  # next suggestion must not overlap this one
        else:
            t += step
    return out
