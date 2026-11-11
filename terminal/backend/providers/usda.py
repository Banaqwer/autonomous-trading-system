"""USDA NASS QuickStats + USDA MARS — crop/ag fundamentals (WX crops overlay).

Keys (baked in config.py, loaded into os.environ):
  USDA_API_KEY        legacy quickstats key (quickstats.nass.usda.gov/api)
  NASS_API_KEY        QuickStats key for cold_storage()/crop_stocks()
  USDA_MARS_API_KEY   USDA Market News (MARS) — basic auth, key as username

Ag-commodity sensor helpers. NASS helpers below never raise (network wrapped,
return [] on any failure). quickstats() keeps its original raising behaviour.
"""
from __future__ import annotations
import os, json, base64, urllib.request, urllib.parse, urllib.error

NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
MARS_BASE = "https://marsapi.ams.usda.gov"


def quickstats(commodity="CORN", statistic="PRODUCTION", year="2024", agg="NATIONAL"):
    key = os.environ.get("USDA_API_KEY") or os.environ.get("NASS_API_KEY")
    if not key:
        raise RuntimeError("USDA_API_KEY not set — free at quickstats.nass.usda.gov/api")
    p = urllib.parse.urlencode({"key": key, "commodity_desc": commodity,
        "statisticcat_desc": statistic, "year": year, "agg_level_desc": agg, "format": "JSON"})
    with urllib.request.urlopen(f"https://quickstats.nass.usda.gov/api/api_GET/?{p}", timeout=30) as r:
        d = json.loads(r.read())
    return [{"commodity": x.get("commodity_desc"), "value": x.get("Value"),
             "unit": x.get("unit_desc"), "year": x.get("year"), "geo": x.get("location_desc")}
            for x in d.get("data", [])][:200]


def _nass_get(params):
    """GET QuickStats JSON. Returns list of raw rows, [] on any failure."""
    key = os.environ.get("NASS_API_KEY") or os.environ.get("USDA_API_KEY")
    if not key:
        return []
    q = {"key": key, "format": "JSON"}
    q.update(params)
    url = f"{NASS_BASE}?{urllib.parse.urlencode(q)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenQuant/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d.get("data", []) or []
    except Exception:
        return []


def _to_num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "(D)", "(Z)", "(NA)", "(X)", "(S)"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _sort_key(row):
    """Best-effort chronological key: (year, month/quarter ordinal)."""
    try:
        yr = int(row.get("year") or 0)
    except Exception:
        yr = 0
    ref = (row.get("reference_period_desc") or "").upper()
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    ord_ = 0
    for abbr, n in months.items():          # matches "END OF JAN", "FIRST OF SEP", "JAN"
        if abbr in ref:
            ord_ = n
            break
    try:
        beg = int(row.get("begin_code") or 0)
    except Exception:
        beg = 0
    return (yr, ord_ or beg)


def cold_storage(commodity="BEEF"):
    """Recent monthly cold-storage stock levels for a commodity.

    Company-stress / ag-supply sensor: rising cold-storage stocks = oversupply
    pressure on the commodity. Returns [{period, value, commodity}] newest last.
    """
    # NASS tags cold-storage series as freq "POINT IN TIME" with reference
    # periods like "END OF JAN" — one row per month. (freq_desc=MONTHLY 400s here.)
    rows = _nass_get({
        "source_desc": "SURVEY",
        "commodity_desc": commodity.upper(),
        "statisticcat_desc": "STOCKS",
        "year__GE": "2024",
    })
    out = []
    for x in rows:
        sd = (x.get("short_desc") or "").upper()
        if "COLD STORAGE" not in sd:   # keep only the cold-storage stocks series
            continue
        val = _to_num(x.get("Value"))
        if val is None:
            continue
        period = f"{x.get('reference_period_desc', '')} {x.get('year', '')}".strip()
        out.append({"period": period, "value": val, "commodity": x.get("commodity_desc"),
                    "unit": x.get("unit_desc"), "series": x.get("short_desc"),
                    "_k": _sort_key(x)})
    out.sort(key=lambda r: r.pop("_k"))
    return out[-60:]


def crop_stocks(commodity="CORN"):
    """Recent quarterly grain stocks for a commodity.

    Ag-commodity sensor: quarterly grain-stocks reports move grain futures.
    Returns [{period, value, commodity}] newest last.
    """
    rows = _nass_get({
        "source_desc": "SURVEY",
        "sector_desc": "CROPS",
        "commodity_desc": commodity.upper(),
        "statisticcat_desc": "STOCKS",
        "freq_desc": "POINT IN TIME",
        "year__GE": "2024",
    })
    if not rows:
        rows = _nass_get({
            "source_desc": "SURVEY",
            "commodity_desc": commodity.upper(),
            "statisticcat_desc": "STOCKS",
            "year__GE": "2024",
        })
    out = []
    for x in rows:
        val = _to_num(x.get("Value"))
        if val is None:
            continue
        period = f"{x.get('reference_period_desc', '')} {x.get('year', '')}".strip()
        out.append({"period": period, "value": val, "commodity": x.get("commodity_desc"),
                    "unit": x.get("unit_desc"), "class": x.get("class_desc"),
                    "geo": x.get("location_desc"), "_k": _sort_key(x)})
    out.sort(key=lambda r: r.pop("_k"))
    return out[-80:]


def mars_reports(q="cattle", limit=25):
    """USDA Market News (MARS) report directory search. Basic auth, key as username.

    Returns [{slug_id, report_title, markets, ...}], [] on failure or no key.
    """
    key = os.environ.get("USDA_MARS_API_KEY")
    if not key:
        return []
    url = f"{MARS_BASE}/services/v1.2/reports"
    if q:
        url += f"?q={urllib.parse.quote(q)}"
    try:
        token = base64.b64encode(f"{key}:".encode()).decode()
        req = urllib.request.Request(url, headers={
            "Authorization": f"Basic {token}", "User-Agent": "OpenQuant/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        rows = d if isinstance(d, list) else d.get("results", d.get("reports", []))
        out = []
        for x in (rows or [])[:limit]:
            if not isinstance(x, dict):
                continue
            out.append({
                "slug_id": x.get("slug_id") or x.get("slugId") or x.get("id"),
                "report_title": x.get("report_title") or x.get("reportTitle") or x.get("title"),
                "markets": x.get("markets") or x.get("market_types"),
                "published": x.get("published_date") or x.get("publishedDate"),
            })
        return out
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/user/openquant/terminal/backend")
    import config  # noqa: F401  (sets os.environ)

    print("=== cold_storage('BEEF') ===")
    cs = cold_storage("BEEF")
    print(f"rows: {len(cs)}")
    if cs:
        print("sample (newest):", json.dumps(cs[-1]))

    print("\n=== crop_stocks('CORN') ===")
    gs = crop_stocks("CORN")
    print(f"rows: {len(gs)}")
    if gs:
        print("sample (newest):", json.dumps(gs[-1]))

    print("\n=== mars_reports('cattle') ===")
    mr = mars_reports("cattle", limit=3)
    print(f"rows: {len(mr)}")
    if mr:
        print("sample:", json.dumps(mr[0]))
