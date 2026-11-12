"""Financial Modeling Prep (/stable/) — earnings calendar (owner: S6; used by the research
app). 250 requests/day. Set FMP_API_KEY. The key travels in the `apikey` HEADER (verified
2026-11-12), never in the URL. Returns [] when unconfigured or on any failure.
(The economic-calendar endpoint is paid: HTTP 402 on this plan.)
"""
from __future__ import annotations

import datetime as dt
import os

from core import http

BASE = "https://financialmodelingprep.com/stable"
SOURCE = "fmp"


def enabled():
    return bool(os.environ.get("FMP_API_KEY"))


def earnings_calendar(days_ahead=7):
    if not enabled():
        return []
    today = dt.date.today()
    frm, to = today.isoformat(), (today + dt.timedelta(days=max(1, min(int(days_ahead), 60)))).isoformat()
    try:
        d = http.get_json(f"{BASE}/earnings-calendar?" + http.qs({"from": frm, "to": to}),
                          {"apikey": os.environ["FMP_API_KEY"]}, 25, source=SOURCE)
    except Exception:
        return []
    rows = d if isinstance(d, list) else (d.get("earningsCalendar", []) if isinstance(d, dict) else [])
    return [{"symbol": r.get("symbol"), "date": r.get("date"),
             "eps_est": r.get("epsEstimated"), "rev_est": r.get("revenueEstimated")} for r in rows if isinstance(r, dict)]
