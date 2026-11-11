"""Financial Modeling Prep (/stable/) — earnings calendar. Causal sensor: an upcoming
earnings date = a scheduled binary event. 250 req/day. Set FMP_API_KEY.
"""
from __future__ import annotations
import os, json, urllib.request, datetime as dt

BASE = "https://financialmodelingprep.com/stable"

def enabled():
    return bool(os.environ.get("FMP_API_KEY"))

def earnings_calendar(days_ahead=7):
    if not enabled():
        return []
    today = dt.date.today()
    frm, to = today.isoformat(), (today + dt.timedelta(days=days_ahead)).isoformat()
    url = f"{BASE}/earnings-calendar?from={frm}&to={to}&apikey={os.environ['FMP_API_KEY']}"
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"}), timeout=25).read())
    except Exception:
        return []
    rows = d if isinstance(d, list) else d.get("earningsCalendar", [])
    return [{"symbol": r.get("symbol"), "date": r.get("date"),
             "eps_est": r.get("epsEstimated"), "rev_est": r.get("revenueEstimated")} for r in rows]
