"""EIA (US Energy Information Administration) API v2 — weekly petroleum fundamentals (owner: S6).

Free key: EIA_API_KEY (eia.gov/opendata/register.php). The key travels in the
`X-Api-Key` header (verified 2026-11-12), never in the URL.

Two calls:

* `fetch_weekly(series, start)` — ONE request to `petroleum/sum/sndw` (the Weekly Supply
  Estimates table) returns every series the supply-chain page needs (19 series x ~109
  weeks = 2,071 rows on 2026-11-12, 1.7 s). The API caps a reply at 5,000 rows.
* `fetch_series(series_id, length)` — the generic `seriesid/<ID>` route (one series).

EIA weekly data is for the week ENDING on a Friday and is published the following
Wednesday 10:30 US Eastern (a day later after a federal holiday). It is WEEKLY data: the
terminal labels it with the week-ending date and never as live.
Units as EIA writes them: MBBL = thousand barrels, MBBL/D = thousand barrels a day.
"""
from __future__ import annotations

import os
import re

from core import http
from core.errors import BadRequest, NotConfigured, UpstreamError
from core.util import date_epoch, num

BASE = "https://api.eia.gov/v2"
SOURCE = "eia"
SERIES_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,11}\.[A-Z0-9_\-]{2,48}\.[AMWDQH]$")     # PET.WCESTUS1.W
FACET = re.compile(r"^[A-Z0-9_]{3,40}$")                                           # WCESTUS1
UNITS = {"MBBL": "kbbl", "MBBL/D": "kb/d", "%": "%", "BCF": "Bcf", "$/BBL": "$/bbl"}
WEEKLY_ROUTE = "petroleum/sum/sndw"


def configured() -> bool:
    return bool(os.environ.get("EIA_API_KEY"))


def _headers() -> dict:
    if not configured():
        raise NotConfigured("EIA_API_KEY is not set — free at eia.gov/opendata/register.php", source=SOURCE)
    return {"X-Api-Key": os.environ["EIA_API_KEY"]}


# ------------------------------------------------------------------ fetch
def fetch_series(series_id: str, length: int = 52, timeout: float = 20) -> dict:
    """Raw v2 `seriesid` payload, newest first as the API sends it."""
    if not SERIES_ID.match(series_id or ""):
        raise BadRequest("series_id must look like PET.WCESTUS1.W")
    length = max(1, min(int(length), 5000))
    return http.get_json(f"{BASE}/seriesid/{series_id}?" + http.qs({"length": length}),
                         _headers(), timeout, source=SOURCE)


def fetch_weekly(series, start: str, timeout: float = 30) -> dict:
    """Raw payload of the weekly supply table for several series since `start` (YYYY-MM-DD)."""
    series = list(series)
    if not series or any(not FACET.match(s) for s in series):
        raise BadRequest("bad EIA series facet")
    if not re.match(r"^\d{4}-\d\d-\d\d$", start or ""):
        raise BadRequest("start must be YYYY-MM-DD")
    query = ("frequency=weekly&data[0]=value&" + "&".join(f"facets[series][]={s}" for s in series)
             + f"&start={start}&sort[0][column]=period&sort[0][direction]=desc&length=5000")
    return http.get_json(f"{BASE}/{WEEKLY_ROUTE}/data/?{query}", _headers(), timeout, source=SOURCE)


# ------------------------------------------------------------------ parse
def _rows(payload) -> list:
    try:
        rows = payload["response"]["data"]
    except (KeyError, TypeError):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (no response.data)") from None
    if not isinstance(rows, list):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (response.data is not a list)")
    return rows


def parse_by_series(payload) -> dict:
    """{series: {"unit", "desc", "obs": [{"date", "t", "value"}…]}} — obs ASCENDING by date,
    one per date, rows without a numeric value dropped. `unit` is the terminal's spelling
    (kbbl, kb/d, %), `desc` is EIA's own series description."""
    out: dict = {}
    for r in _rows(payload):
        if not isinstance(r, dict):
            continue
        sid, date, value = r.get("series"), r.get("period"), num(r.get("value"))
        t = date_epoch(date) if isinstance(date, str) else None
        if not sid or t is None or value is None:
            continue
        s = out.setdefault(sid, {"unit": UNITS.get(str(r.get("units") or "").upper(), r.get("units")),
                                 "desc": r.get("series-description"), "obs": {}})
        s["obs"][date[:10]] = {"date": date[:10], "t": t, "value": value}
    for s in out.values():
        s["obs"] = [s["obs"][d] for d in sorted(s["obs"])]
    return out


def parse_series(payload) -> list:
    """[{date, t, value}] ascending for a single-series payload."""
    by = parse_by_series(payload)
    if not by:
        return []
    if len(by) > 1:
        raise UpstreamError(SOURCE, None, "reply mixes several series")
    return next(iter(by.values()))["obs"]


def series(series_id: str = "PET.WCESTUS1.W", length: int = 52) -> list:
    """[{date, value}] OLDEST FIRST (the last row is the latest week). Kept for the research
    app (quantpad/backend/signals.py reads [-1] as the latest and [-2] as the prior week;
    the pre-rebuild version returned newest first, which made that comparison backwards)."""
    return [{"date": o["date"], "value": o["value"]} for o in parse_series(fetch_series(series_id, length))]
