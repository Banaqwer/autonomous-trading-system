"""LaborCurrent — national WARN (layoff) notices. Causal sensor: mass layoffs =
company/sector stress. Eval tier: 30-day delayed, ~10 calls/day. Set LABORCURRENT_API_KEY.
"""
from __future__ import annotations
import os, json, urllib.request

def enabled():
    return bool(os.environ.get("LABORCURRENT_API_KEY"))

def recent_warn(limit=30):
    if not enabled():
        return []
    req = urllib.request.Request("https://laborcurrent.com/api/records?limit=%d" % limit,
                                 headers={"X-API-Key": os.environ["LABORCURRENT_API_KEY"], "User-Agent": "openterm/0.1"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return []
    rows = d.get("records", d.get("data", d if isinstance(d, list) else []))
    out = []
    for r in rows:
        out.append({"company": r.get("company") or r.get("employer") or r.get("company_name"),
                    "state": r.get("state"), "employees": r.get("employees") or r.get("affected") or r.get("num_employees"),
                    "date": r.get("date") or r.get("notice_date") or r.get("received_date")})
    return out
