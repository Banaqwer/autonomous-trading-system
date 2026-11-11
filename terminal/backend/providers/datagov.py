"""data.gov — one API key (DATA_GOV_API_KEY) unlocks both SAM.gov and
Regulations.gov. Causal sensors:
  - sam_awards: federal contract award notices -> contract/revenue sensor
    (big award -> the awardee / its sector).
  - regulations_recent: new federal regulatory documents -> policy sensor
    (rulemaking / proposed rule -> the affected industry).
Stdlib only. Never raises; returns [] on any failure.
"""
from __future__ import annotations
import os, json, urllib.request, urllib.parse
from datetime import date, timedelta

_UA = {"User-Agent": "openterm/0.1"}
SAM_URL = "https://api.sam.gov/opportunities/v2/search"
REG_URL = "https://api.regulations.gov/v4/documents"


def _get(url, params, headers=None):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q, headers={**_UA, **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def sam_awards(naics=None, limit=20):
    """Recent federal contract AWARD notices (ptype=a) from SAM.gov.

    Returns [{title, awardee, amount, agency, date}]. SAM requires a
    postedFrom/postedTo window (MM/DD/YYYY, <=1yr span); we use the last 30 days.
    """
    key = os.environ.get("DATA_GOV_API_KEY")
    if not key:
        return []
    today = date.today()
    params = {
        "api_key": key,
        "ptype": "a",                       # a = Award Notice
        "limit": max(1, min(int(limit), 100)),
        "postedFrom": (today - timedelta(days=30)).strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
    }
    if naics:
        params["ncode"] = str(naics)
    try:
        d = _get(SAM_URL, params)
        out = []
        for o in d.get("opportunitiesData", []) or []:
            award = o.get("award") or {}
            awardee = (award.get("awardee") or {}).get("name")
            out.append({
                "title": o.get("title"),
                "awardee": awardee,
                "amount": award.get("amount"),
                "agency": o.get("fullParentPathName") or o.get("organizationType"),
                "date": o.get("postedDate"),
            })
        return out
    except Exception:
        return []


def regulations_recent(term="", limit=20):
    """Recent Regulations.gov documents, newest first.

    Returns [{title, agency, docketId, date}].
    """
    key = os.environ.get("DATA_GOV_API_KEY")
    if not key:
        return []
    params = {
        "api_key": key,
        "sort": "-postedDate",
        "page[size]": max(5, min(int(limit), 250)),
    }
    if term:
        params["filter[searchTerm]"] = term
    try:
        d = _get(REG_URL, params)
        out = []
        for doc in (d.get("data", []) or [])[:int(limit)]:
            a = doc.get("attributes", {}) or {}
            out.append({
                "title": a.get("title"),
                "agency": a.get("agencyId"),
                "docketId": a.get("docketId"),
                "date": a.get("postedDate"),
            })
        return out
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/user/openquant/terminal/backend")
    import config  # noqa: sets os.environ
    print("=== sam_awards ===")
    sa = sam_awards(limit=5)
    print(f"count={len(sa)}")
    if sa:
        print(json.dumps(sa[0], indent=2))
    print("=== regulations_recent ===")
    rr = regulations_recent(term="emissions", limit=5)
    print(f"count={len(rr)}")
    if rr:
        print(json.dumps(rr[0], indent=2))
