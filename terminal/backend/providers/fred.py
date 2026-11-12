"""FRED (Federal Reserve Bank of St. Louis) — macro series (owner: S6).

Free key: FRED_API_KEY. The API accepts the key only as the `api_key` query parameter
(no header form); core.http scrubs it from every error message and nothing here returns
or logs a URL. An observation's `realtime_start` is the day that value was published.
"""
from __future__ import annotations

import os
import re

from core import http
from core.errors import BadRequest, NotConfigured, UpstreamError
from core.util import date_epoch, num

BASE = "https://api.stlouisfed.org/fred"
SOURCE = "fred"
SERIES_ID = re.compile(r"^[A-Z0-9_]{1,40}$")


def configured() -> bool:
    return bool(os.environ.get("FRED_API_KEY"))


def fetch_observations(series_id: str, limit: int = 120, timeout: float = 15) -> dict:
    """Raw payload, NEWEST first (sort_order=desc) so `limit` keeps the latest values."""
    if not SERIES_ID.match(series_id or ""):
        raise BadRequest("series_id must be a FRED id such as DGS10 (letters, digits, underscore)")
    if not configured():
        raise NotConfigured("FRED_API_KEY is not set — free at fred.stlouisfed.org", source=SOURCE)
    params = {"series_id": series_id, "api_key": os.environ["FRED_API_KEY"], "file_type": "json",
              "sort_order": "desc", "limit": max(1, min(int(limit), 2000))}
    return http.get_json(f"{BASE}/series/observations?" + http.qs(params), timeout=timeout, source=SOURCE)


def parse_observations(payload) -> list:
    """[{date, t, value, published}] ASCENDING; '.' (no value) rows keep value None."""
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (no observations)")
    out = []
    for o in payload["observations"]:
        t = date_epoch(o.get("date")) if isinstance(o, dict) else None
        if t is None:
            continue
        out.append({"date": o["date"][:10], "t": t, "value": num(o.get("value")),
                    "published": (o.get("realtime_start") or None)})
    out.sort(key=lambda x: x["t"])
    return out


def series(series_id="DCOILWTICO", limit=120):
    """[{date, value}] oldest first (old signature)."""
    return [{"date": o["date"], "value": o["value"]} for o in parse_observations(fetch_observations(series_id, limit))]
