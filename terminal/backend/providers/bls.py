"""BLS public API v2 (owner: S6) — official US labour and price series.

POST https://api.bls.gov/publicAPI/v2/timeseries/data/ with a JSON body; the
registration key (BLS_API_KEY) travels in the BODY, never in a URL. With a key and
`calculations: true` the reply carries BLS's own 1/3/6/12-month net and percent
changes — the published figures, so nothing has to be recomputed (verified 2026-11-12:
CUSR0000SA0 October 2026 pct_changes["1"] = "0.4"). Up to 50 series per request.

`series()` keeps the contract the research app (quantpad) uses: [(period, value)]
oldest first, [] on any failure.
"""
from __future__ import annotations

import os
import re
import time

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import num

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SOURCE = "bls"
_SERIES = re.compile(r"^[A-Z0-9_#\-]{4,32}$")


def fetch_many(series_ids, startyear: int | None = None, endyear: int | None = None,
               calculations: bool = True, timeout: float = 20) -> dict:
    ids = [s for s in series_ids]
    if not ids or len(ids) > 50 or any(not _SERIES.match(s or "") for s in ids):
        raise BadRequest("1–50 BLS series ids expected")
    year = time.gmtime(time.time()).tm_year
    body = {"seriesid": ids, "startyear": str(int(startyear or year - 1)), "endyear": str(int(endyear or year))}
    if os.environ.get("BLS_API_KEY"):
        body["registrationkey"] = os.environ["BLS_API_KEY"]
        body["calculations"] = bool(calculations)
    return http.post_json(URL, body, timeout=timeout, source=SOURCE)


def parse_many(payload) -> dict:
    """{series_id: [{period:"2026-10", year, month, value, pct:{"1":0.4,…}, net:{…}, preliminary}]} newest first.
    Only monthly rows (M01–M12)."""
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        msg = "; ".join(str(m) for m in (payload.get("message") or [])[:2]) if isinstance(payload, dict) else ""
        raise UpstreamError(SOURCE, None, f"request not successful ({msg[:160] or 'no status'})")
    out = {}
    try:
        series = payload["Results"]["series"]
    except (KeyError, TypeError):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (no Results.series)") from None
    for s in series:
        rows = []
        for r in s.get("data") or []:
            m = re.match(r"^M(0[1-9]|1[0-2])$", str(r.get("period") or ""))
            value = num(r.get("value"))
            if not m or value is None or not str(r.get("year") or "").isdigit():
                continue
            calc = r.get("calculations") or {}
            rows.append({"period": f"{r['year']}-{m.group(1)}", "year": int(r["year"]), "month": int(m.group(1)),
                         "value": value,
                         "pct": {k: num(v) for k, v in (calc.get("pct_changes") or {}).items() if num(v) is not None},
                         "net": {k: num(v) for k, v in (calc.get("net_changes") or {}).items() if num(v) is not None},
                         "preliminary": any((f or {}).get("code") == "P" for f in (r.get("footnotes") or []))})
        rows.sort(key=lambda x: x["period"], reverse=True)
        out[s.get("seriesID")] = rows
    return out


def series(series_id="WPUFD4", start=None, end=None):
    """WPUFD4 = PPI final demand. Returns [(period, value)] newest last; [] on failure."""
    try:
        rows = parse_many(fetch_many([series_id], start, end, calculations=False)).get(series_id) or []
        return [(f"{r['year']}-M{r['month']:02d}", r["value"]) for r in reversed(rows)]
    except Exception:
        return []
