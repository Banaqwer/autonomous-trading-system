"""US Treasury yields, end of day (owner: FB).

  par_curve(year)      Treasury.gov daily par yield curve CSV (keyless) — the US2Y source
  fred_series(id)      FRED DGS2 / DGS10 (needs FRED_API_KEY) — the fallback; FRED lags
                       Treasury.gov by about one publication

END OF DAY ONLY (published after the US close). No free intraday 2-year yield exists.
"""
from __future__ import annotations

import csv
import io
import os

from core import http
from core.errors import NotConfigured, UpstreamError
from core.util import date_epoch, num

BASE = "https://home.treasury.gov"
FRED_BASE = "https://api.stlouisfed.org/fred"
SOURCE = "treasury"


def par_curve(year: int, timeout: float = 15) -> str:
    url = (f"{BASE}/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           f"{int(year)}/all?type=daily_treasury_yield_curve&field_tdr_date_value={int(year)}"
           f"&page&_format=csv")
    return http.get_text(url, {"Accept": "text/csv,*/*"}, timeout, source=SOURCE)


def parse_curve(text: str) -> list[dict]:
    """[{date:'YYYY-MM-DD', t, values:{'2 Yr': 4.73, '10 Yr': 4.98, …}}…] ascending by date."""
    rows = []
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        raise UpstreamError(SOURCE, None, "unexpected CSV layout (no 'Date' column)")
    for r in reader:
        raw = (r.get("Date") or "").strip()
        parts = raw.split("/")
        if len(parts) != 3:
            continue
        iso = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        t = date_epoch(iso)
        if t is None:
            continue
        values = {k: num(v) for k, v in r.items() if k and k != "Date"}
        rows.append({"date": iso, "t": int(t), "values": {k: v for k, v in values.items() if v is not None}})
    rows.sort(key=lambda r: r["t"])
    return rows


def series(rows: list[dict], column: str) -> list[dict]:
    """[{date, t, value}…] for one tenor column ('2 Yr'), rows without a value skipped."""
    return [{"date": r["date"], "t": r["t"], "value": r["values"][column]}
            for r in rows if column in r["values"]]


def fred_series(series_id: str, limit: int = 10, timeout: float = 10) -> dict:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise NotConfigured("FRED_API_KEY is not set", source="fred")
    url = f"{FRED_BASE}/series/observations?" + http.qs({
        "series_id": series_id, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": max(1, min(int(limit), 2000))})
    return http.get_json(url, timeout=timeout, source="fred")


def parse_fred(payload: dict) -> list[dict]:
    """[{date, t, value}…] ascending; FRED's '.' (no observation) rows are skipped."""
    out = []
    for o in (payload or {}).get("observations") or []:
        value, t = num(o.get("value")), date_epoch(o.get("date"))
        if value is None or t is None:
            continue
        out.append({"date": o["date"][:10], "t": int(t), "value": value})
    out.sort(key=lambda r: r["t"])
    return out
