"""openFDA — drug shortages & enforcement recalls (owner: S6; used by the research app).
Public API; OPENFDA_API_KEY only raises the rate limit (works without a key). The key is
sent as HTTP basic auth (user name = key), never in the URL. Returns [] on any failure.
"""
from __future__ import annotations

import base64
import os

from core import http

BASE = "https://api.fda.gov"
SOURCE = "openfda"


def _get(path, **params):
    headers = None
    if os.environ.get("OPENFDA_API_KEY"):          # HTTP basic auth, key as the user name: nothing in the URL
        token = base64.b64encode((os.environ["OPENFDA_API_KEY"] + ":").encode()).decode()
        headers = {"Authorization": "Basic " + token}
    return http.get_json(f"{BASE}{path}?" + http.qs(params), headers, 25, source=SOURCE)


def drug_shortages(limit=100):
    """Current drug shortages. Returns list of {name, status, therapeutic_category}."""
    try:
        d = _get("/drug/shortages.json", limit=max(1, min(int(limit), 1000)))
    except Exception:
        return []
    out = []
    for x in d.get("results", []) if isinstance(d, dict) else []:
        out.append({"name": x.get("proprietary_name") or x.get("generic_name") or x.get("company_name"),
                    "status": x.get("status"), "category": x.get("therapeutic_category")})
    return out


def recent_recalls(days=30, limit=50):
    """Recent drug enforcement (recall) reports."""
    try:
        d = _get("/drug/enforcement.json", sort="report_date:desc", limit=max(1, min(int(limit), 1000)))
    except Exception:
        return []
    return [{"firm": x.get("recalling_firm"), "reason": (x.get("reason_for_recall") or "")[:120],
             "classification": x.get("classification"), "date": x.get("report_date")}
            for x in (d.get("results", []) if isinstance(d, dict) else [])]
