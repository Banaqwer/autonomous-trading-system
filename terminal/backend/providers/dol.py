"""DOL Open Data Portal (v4) — OSHA enforcement. Causal sensor: a cluster of OSHA
inspections / safety citations at an establishment = industrial-event / operational-risk
signal (plant fire, chemical release, mass-casualty event -> the operator's stock).

DOL v4 accepts the API key ONLY as the X-API-KEY *query* parameter (uppercase),
never as a header. Set DOL_API_KEY. Dataset catalog + docs: dataportal.dol.gov.

Data path (discovered live):
    https://api.dol.gov/v4/get/OSHA/{dataset}/json?X-API-KEY=...&limit=...&fields=...&filter_object=...
Supported query params: limit (1..10000), offset, fields (csv), filter_object
(JSON {"field","operator","value"}; operators incl. eq/gt/lt). There is NO working
server-side sort, so "recent" is done by filtering open_date to a rolling window and
sorting client-side. OSHA inspection = case detail; OSHA violation = citations, joined
on activity_nr (newly opened inspections usually have no citations issued yet).
"""
from __future__ import annotations
import os, json, urllib.parse, urllib.request, urllib.error
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor

_BASE = "https://api.dol.gov/v4/get/OSHA/%s/json"


def enabled():
    return bool(os.environ.get("DOL_API_KEY"))


def _get(dataset, params):
    """Return list-of-dicts for an OSHA dataset, or [] on any failure/no-content."""
    key = os.environ.get("DOL_API_KEY")
    if not key:
        return []
    q = {"X-API-KEY": key}
    q.update(params)
    url = _BASE % dataset + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"})
    try:
        resp = urllib.request.urlopen(req, timeout=45)
        if resp.status == 204:           # no rows match -> empty
            return []
        body = resp.read().decode()
        if not body.strip():
            return []
        return json.loads(body).get("data", []) or []
    except Exception:
        return []


def _violation_count(activity_nr):
    """Number of OSHA citations on one inspection (0 if none / on failure)."""
    try:
        rows = _get("violation", {
            "limit": 500,
            "fields": "activity_nr,citation_id,issuance_date,current_penalty,initial_penalty",
            "filter_object": json.dumps({"field": "activity_nr", "operator": "eq",
                                         "value": int(activity_nr)}),
        })
        return len(rows)
    except Exception:
        return 0


def recent_inspections(limit=50, days=45, with_violations=True):
    """Most-recent OSHA inspections, newest first.

    -> [{establishment, state, date, violations}]
       (also carries activity_nr, city, naics for downstream mapping)

    Filters inspections opened within the last `days`, sorts client-side by open
    date descending, and (if with_violations) joins the citation count per case.
    Never raises; returns [] on any failure.
    """
    if not enabled():
        return []
    try:
        cutoff = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
        rows = _get("inspection", {
            "limit": 10000,
            "fields": "activity_nr,estab_name,site_state,site_city,open_date,naics_code",
            "filter_object": json.dumps({"field": "open_date", "operator": "gt",
                                         "value": cutoff}),
        })
        rows.sort(key=lambda r: (r.get("open_date") or "", r.get("activity_nr") or 0),
                  reverse=True)
        top = rows[: max(0, int(limit))]

        counts = {}
        if with_violations and top:
            acts = [r.get("activity_nr") for r in top if r.get("activity_nr") is not None]
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for a, c in zip(acts, ex.map(_violation_count, acts)):
                        counts[a] = c
            except Exception:
                counts = {}

        out = []
        for r in top:
            a = r.get("activity_nr")
            od = r.get("open_date") or ""
            out.append({
                "establishment": r.get("estab_name"),
                "state": r.get("site_state"),
                "date": od[:10] if od else None,
                "violations": counts.get(a) if with_violations else None,
                "activity_nr": a,
                "city": r.get("site_city"),
                "naics": r.get("naics_code"),
            })
        return out
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/user/openquant/terminal/backend")
    import config  # noqa: F401  (populates os.environ)

    print("enabled:", enabled())
    recs = recent_inspections(limit=8)
    print("returned:", len(recs))
    for r in recs[:8]:
        print(r)
