"""SEC EDGAR — corporate-event stream (8-K) + CIK↔ticker map. Keyless; SEC requires
a descriptive User-Agent (set SEC_USER_AGENT; a generic contact is fine).
An 8-K is a 'material event' filing — the backbone of event-driven equity trading.
"""
from __future__ import annotations
import os, re, json, urllib.request, time

def _ua():
    return {"User-Agent": os.environ.get("SEC_USER_AGENT", "OpenQuant Research research@example.com")}

def recent_8k(count=40):
    url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
           f"&company=&dateb=&owner=include&count={count}&output=atom")
    try:
        x = urllib.request.urlopen(urllib.request.Request(url, headers=_ua()), timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for t in re.findall(r"<title>([^<]+)</title>", x)[1:]:
        m = re.match(r"8-K\s*-\s*(.+?)\s*\((\d{10})\)", t)
        if m:
            out.append({"company": m.group(1).strip(), "cik": int(m.group(2))})
    return out

_TMAP = {"ts": 0, "map": {}}
def cik_to_ticker():
    """SEC company_tickers.json -> {cik: TICKER}. Cached 24h."""
    if _TMAP["map"] and time.time() - _TMAP["ts"] < 86400:
        return _TMAP["map"]
    try:
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers=_ua()), timeout=25).read())
        m = {int(v["cik_str"]): v["ticker"] for v in d.values()}
        _TMAP.update(ts=time.time(), map=m)
    except Exception:
        pass
    return _TMAP["map"]
