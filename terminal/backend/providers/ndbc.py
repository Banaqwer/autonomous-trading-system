"""NOAA National Data Buoy Center — the latest MEASURED marine observation of every station. Keyless.

    latest_obs.txt       one whitespace-separated row per station (about 840 rows, 100 kB, rewritten every
                         few minutes): position, observation time (UTC), wind direction / speed / gust (m/s),
                         significant wave height (m) and period (s), pressure (hPa), air and water temperature
    station_table.txt    station id -> name ("MID GULF - 180 nm South of Southwest Pass, LA"); changes rarely

`MM` in the file means "this sensor did not report". Wind is converted to knots here and the value as
reported (m/s) is kept beside it. One function per upstream call; the parsers are pure.
"""
from __future__ import annotations

import calendar
import html as _html
import re

from core import http
from core.errors import UpstreamError

SOURCE = "ndbc"
LATEST_URL = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
STATIONS_URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"
MS_TO_KT = 1.943844
_NEEDED = ("STN", "LAT", "LON", "YYYY", "MM", "DD", "hh", "mm")


def fetch_latest(timeout: float = 15) -> str:
    return http.get_text(LATEST_URL, timeout=timeout, source=SOURCE)


def fetch_stations(timeout: float = 20) -> str:
    return http.get_text(STATIONS_URL, timeout=timeout, source=SOURCE)


def _f(tok):
    if tok is None or tok == "MM":
        return None
    try:
        return float(tok)
    except ValueError:
        return None


def _kt(ms):
    return None if ms is None else round(ms * MS_TO_KT, 1)


def parse_latest(text: str) -> list[dict]:
    """latest_obs.txt -> [{id, lat, lon, t, wind_dir, wind_kt, wind_ms, gust_kt, gust_ms, wave_m, wave_period_s,
    wave_dir, pressure_mb, pressure_tendency_mb, air_c, water_c}] — None where the sensor did not report."""
    lines = (text or "").splitlines()
    header = next((ln for ln in lines if ln.startswith("#") and "STN" in ln), None)
    if header is None:
        raise UpstreamError(SOURCE, None, "latest_obs.txt has no column header")
    cols = header.lstrip("#").split()
    col = {name: i for i, name in enumerate(cols)}
    if any(n not in col for n in _NEEDED):
        raise UpstreamError(SOURCE, None, "latest_obs.txt columns changed: " + " ".join(cols)[:120])

    def get(row, name):
        i = col.get(name)
        return _f(row[i]) if i is not None and i < len(row) else None

    out = []
    for ln in lines:
        if not ln.strip() or ln.startswith("#"):
            continue
        row = ln.split()
        if len(row) < len(_NEEDED):
            continue
        lat, lon = get(row, "LAT"), get(row, "LON")
        try:
            t = calendar.timegm((int(row[col["YYYY"]]), int(row[col["MM"]]), int(row[col["DD"]]),
                                 int(row[col["hh"]]), int(row[col["mm"]]), 0))
        except (ValueError, OverflowError):
            continue
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            continue
        wspd, gst = get(row, "WSPD"), get(row, "GST")
        wdir, mwd = get(row, "WDIR"), get(row, "MWD")
        out.append({
            "id": row[col["STN"]], "lat": lat, "lon": lon, "t": float(t),
            "wind_dir": wdir, "wind_kt": _kt(wspd), "wind_ms": wspd, "gust_kt": _kt(gst), "gust_ms": gst,
            "wave_m": get(row, "WVHT"), "wave_period_s": get(row, "DPD"), "wave_dir": mwd,
            "pressure_mb": get(row, "PRES"), "pressure_tendency_mb": get(row, "PTDY"),
            "air_c": get(row, "ATMP"), "water_c": get(row, "WTMP"),
        })
    if not out:
        raise UpstreamError(SOURCE, None, "latest_obs.txt holds no observation rows")
    return out


def parse_stations(text: str) -> dict:
    """station_table.txt ('|'-separated) -> {station id: name}."""
    out = {}
    for ln in (text or "").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split("|")
        if len(p) < 5:
            continue
        name = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", p[4]))).strip()
        if p[0].strip() and name:
            out[p[0].strip().upper()] = name[:80]
    return out
