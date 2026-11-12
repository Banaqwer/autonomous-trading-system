"""LaborCurrent — national WARN (layoff) notices (owner: S6; used by the research app).
Eval tier: 30-day delayed, ~10 calls/day. Set LABORCURRENT_API_KEY (sent in the X-API-Key
header). Returns [] when unconfigured or on any failure.
"""
from __future__ import annotations

import os

from core import http

SOURCE = "laborcurrent"


def enabled():
    return bool(os.environ.get("LABORCURRENT_API_KEY"))


def recent_warn(limit=30):
    if not enabled():
        return []
    try:
        d = http.get_json("https://laborcurrent.com/api/records?" + http.qs({"limit": max(1, min(int(limit), 200))}),
                          {"X-API-Key": os.environ["LABORCURRENT_API_KEY"]}, 20, source=SOURCE)
    except Exception:
        return []
    rows = d.get("records", d.get("data", [])) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({"company": r.get("company") or r.get("employer") or r.get("company_name"),
                    "state": r.get("state"), "employees": r.get("employees") or r.get("affected") or r.get("num_employees"),
                    "date": r.get("date") or r.get("notice_date") or r.get("received_date")})
    return out
