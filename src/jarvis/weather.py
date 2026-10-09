import logging
from datetime import datetime
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)
API = "https://api.open-meteo.com/v1/forecast"
GEO = "https://geocoding-api.open-meteo.com/v1/search"
# daily high (°C) below which it is cold for the season, northern-hemisphere months; ponytail: fixed thresholds, not real climatology
COLD_BELOW = {12: 3, 1: 2, 2: 3, 3: 8, 4: 11, 5: 14, 6: 17, 7: 19, 8: 19, 9: 15, 10: 10, 11: 6}
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer",
          8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def advice(high: float, rain_pct: int, month: int, southern: bool = False) -> str:
    """Plain-English 'what to carry' line from today's forecast."""
    m = (month + 5) % 12 + 1 if southern else month
    out = []
    if rain_pct >= 50:
        out.append(f"there's a {rain_pct} percent chance of rain, so carry an umbrella")
    if high < COLD_BELOW[m]:
        s = SEASON[m]
        out.append(f"it's unusually cold for {'an' if s == 'autumn' else 'a'} {s} day, so remember to take a jacket")
    return " and ".join(out)


@lru_cache(maxsize=8)
def _locate(city: str) -> tuple[float, float]:
    r = httpx.get(GEO, params={"name": city, "count": 1}, timeout=8).raise_for_status().json()["results"][0]
    return r["latitude"], r["longitude"]


def load_coords(store) -> tuple[float, float] | None:
    """Last phone location the app reported, or None."""
    raw = store.get_state("last_location")
    return tuple(float(x) for x in raw.split(",")) if raw else None  # type: ignore[return-value]


def save_coords(store, lat: float, lon: float) -> None:
    store.set_state("last_location", f"{lat:.4f},{lon:.4f}")


def fetch(city: str, tz: str, now: datetime, coords: tuple[float, float] | None = None) -> str | None:
    """One sentence about today's weather at the phone's `coords` (else `city`), or None when unavailable."""
    if not (coords or city):
        return None
    try:
        lat, lon = coords or _locate(city)
        d = httpx.get(API, params={"latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 1,
                                   "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"},
                      timeout=8).raise_for_status().json()["daily"]
        hi, lo, rain = round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0]), d["precipitation_probability_max"][0] or 0
        tip = advice(hi, rain, now.month, southern=lat < 0)
        where = "where you are" if coords else f"in {city}"
        return f"Today {where} it's between {lo} and {hi} degrees" + (f", and {tip}." if tip else ".")
    except Exception:
        log.exception("weather lookup failed")
        return None
