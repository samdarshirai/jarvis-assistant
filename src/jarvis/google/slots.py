from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    """Slot must lie wholly inside [start, end] local time in `tz`, on one calendar day there."""
    tz: str
    start: time
    end: time


def _ceil(t: datetime, step: timedelta) -> datetime:
    base = t.replace(minute=0, second=0, microsecond=0)
    n = -(-(t - base) // step)  # ceiling division on timedeltas
    return base + n * step


def _in_window(start: datetime, end: datetime, w: Window) -> bool:
    z = ZoneInfo(w.tz)
    s, e = start.astimezone(z), end.astimezone(z)
    return s.date() == e.date() and w.start <= s.time() and e.time() <= w.end


def free_slots(busy, range_start, range_end, duration, windows=(), step=timedelta(minutes=30), limit=3):
    out = []
    t = _ceil(range_start, step)
    while t + duration <= range_end and len(out) < limit:
        end = t + duration
        clash = any(b0 < end and t < b1 for b0, b1 in busy)
        if not clash and all(_in_window(t, end, w) for w in windows):
            out.append((t, end))
            t = end  # next suggestion must not overlap this one
        else:
            t += step
    return out
