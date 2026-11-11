"""openFDA — drug shortages & enforcement recalls (causal sensor).
Public API; OPENFDA_API_KEY only raises the rate limit (works without a key).
"""
from __future__ import annotations
import os, json, urllib.request, urllib.parse

BASE = "https://api.fda.gov"

def _get(path, **params):
    if os.environ.get("OPENFDA_API_KEY"):
        params["api_key"] = os.environ["OPENFDA_API_KEY"]
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"}), timeout=25) as r:
        return json.loads(r.read())

def drug_shortages(limit=100):
    """Current drug shortages. Returns list of {name, status, therapeutic_category}."""
    try:
        d = _get("/drug/shortages.json", limit=limit)
    except Exception:
        return []
    out = []
    for x in d.get("results", []):
        out.append({"name": x.get("proprietary_name") or x.get("generic_name") or x.get("company_name"),
                    "status": x.get("status"), "category": x.get("therapeutic_category")})
    return out

def recent_recalls(days=30, limit=50):
    """Recent drug enforcement (recall) reports."""
    try:
        d = _get("/drug/enforcement.json", sort="report_date:desc", limit=limit)
    except Exception:
        return []
    return [{"firm": x.get("recalling_firm"), "reason": (x.get("reason_for_recall") or "")[:120],
             "classification": x.get("classification"), "date": x.get("report_date")}
            for x in d.get("results", [])]
