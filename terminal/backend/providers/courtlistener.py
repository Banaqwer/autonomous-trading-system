"""CourtListener — litigation-event sensor.

Maps federal/state court dockets to a causal signal: a major new suit against a
company is a legal-risk event for that defendant (e.g. a big product-liability
or antitrust complaint -> short the defendant / its sector).

Uses the CourtListener v4 search API (RECAP dockets, type=r):
  https://www.courtlistener.com/api/rest/v4/search/?q=<query>&type=r
Auth is an API token sent as an HTTP header: "Authorization: Token <token>".
Reads COURTLISTENER_API_TOKEN from the environment.

Stdlib only. Never raises; returns [] on any failure (including no token).

NOTE: no token is set in this environment, so recent_suits() returns [] on the
no-token path. The request/parsing is built to the documented v4 shape but is
UNTESTED against the live API.
"""
from __future__ import annotations
import os, json

from core import http

SOURCE = "courtlistener"

BASE = "https://www.courtlistener.com/api/rest/v4/search/"


def enabled():
    return bool(os.environ.get("COURTLISTENER_API_TOKEN"))


def _get(params):
    """GET the v4 search endpoint with the token auth header. Returns parsed JSON."""
    token = os.environ["COURTLISTENER_API_TOKEN"]
    return http.get_json(BASE + "?" + http.qs(params), {"Authorization": "Token " + token}, 30, source=SOURCE)


def recent_suits(query="", nature_of_suit=None, limit=20):
    """Recent litigation dockets matching a query -> legal-event records.

    Args:
        query: full-text search string (party name, company, keyword). Empty
            string returns the newest dockets regardless of text.
        nature_of_suit: optional RECAP nature-of-suit filter (string, e.g.
            "830" patent, or a text fragment); forwarded to the API verbatim.
        limit: max records to return.

    Returns a list of dicts:
        [{case_name, court, date_filed, nature_of_suit, docket}]

    Returns [] on any failure or when COURTLISTENER_API_TOKEN is unset.
    """
    if not enabled():
        return []
    params = {
        "q": str(query or "")[:200],
        "type": "r",                 # r = RECAP (dockets / filings)
        "order_by": "dateFiled desc",
    }
    if nature_of_suit:
        params["nature_of_suit"] = str(nature_of_suit)
    try:
        d = _get(params)
        out = []
        for res in (d.get("results", []) or [])[: int(limit)]:
            out.append({
                "case_name": res.get("caseName"),
                "court": res.get("court") or res.get("court_id"),
                "date_filed": res.get("dateFiled"),
                "nature_of_suit": res.get("suitNature"),
                "docket": res.get("docketNumber") or res.get("docket_id"),
            })
        return out
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config  # noqa: sets os.environ from baked defaults

    print("enabled =", enabled())
    r = recent_suits(query="Boeing", limit=5)
    print("recent_suits count =", len(r))
    if r:
        print(json.dumps(r[0], indent=2))
    else:
        print("no data (expected: no COURTLISTENER_API_TOKEN in this env)")
