"""Open-Meteo — MODEL values (forecast-model output at a grid point, not an observation). Keyless.

Used only for reference points where no measured observation exists (the chokepoints outside
NDBC's coverage). Two calls, each for all points at once, both "current" blocks (15-minute steps):

    api.open-meteo.com/v1/forecast          10 m wind speed (kt), direction, gusts, sea-level pressure
    marine-api.open-meteo.com/v1/marine     wave height / direction / period, sea-surface temperature

The free tier is limited per client IP (10 000 calls a day) and answers HTTP 429 when it is used up:
callers cache hard (see routes/geo.py). The coordinates are fixed reference points of the gateway —
a client never supplies them.
"""
from __future__ import annotations

from core import http
from core.errors import UpstreamError
from core.util import iso_epoch, num

SOURCE = "open-meteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_FIELDS = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl")
MARINE_FIELDS = ("wave_height", "wave_direction", "wave_period", "sea_surface_temperature")


def _coords(points) -> dict:
    pts = [(float(p[0]), float(p[1])) for p in points]
    if not pts or len(pts) > 50 or any(abs(la) > 90 or abs(lo) > 180 for la, lo in pts):
        raise ValueError("open-meteo: 1..50 valid (lat, lon) points are required")
    return {"latitude": ",".join(f"{la:.3f}" for la, _ in pts), "longitude": ",".join(f"{lo:.3f}" for _, lo in pts)}


def fetch_forecast(points, timeout: float = 10):
    """points = [(lat, lon), …] -> the raw answer (a list, one object per point; a single object for one point)."""
    q = dict(_coords(points), current=",".join(FORECAST_FIELDS), wind_speed_unit="kn")
    return http.get_json(FORECAST_URL + "?" + http.qs(q), timeout=timeout, source=SOURCE)


def fetch_marine(points, timeout: float = 10):
    q = dict(_coords(points), current=",".join(MARINE_FIELDS))
    return http.get_json(MARINE_URL + "?" + http.qs(q), timeout=timeout, source=SOURCE)


def parse_current(payload, n: int, fields) -> list[dict]:
    """The "current" block of each of the n points -> [{t, grid_lat, grid_lon, <field>: value|None, …}].
    Raises UpstreamError when the answer is Open-Meteo's error object or has another shape."""
    if isinstance(payload, dict) and payload.get("error"):
        raise UpstreamError(SOURCE, None, str(payload.get("reason") or "Open-Meteo reported an error")[:200])
    rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else None
    if rows is None or len(rows) != n or any(not isinstance(r, dict) or not isinstance(r.get("current"), dict) for r in rows):
        raise UpstreamError(SOURCE, None, "Open-Meteo answered in an unexpected shape")
    out = []
    for r in rows:
        cur = r["current"]
        row = {"t": iso_epoch(cur.get("time")), "grid_lat": num(r.get("latitude")), "grid_lon": num(r.get("longitude"))}
        for f in fields:
            row[f] = num(cur.get(f))
        out.append(row)
    return out
