"""US Census Bureau international trade (causal sensor). Monthly imports by HS code.
Set CENSUS_API_KEY (api.census.gov/data/key_signup.html).
"""
from __future__ import annotations
import os, json, urllib.request, urllib.parse

BASE = "https://api.census.gov/data/timeseries/intltrade/imports/hs"

def enabled():
    return bool(os.environ.get("CENSUS_API_KEY"))

def imports_hs(hs="2709", months=8):
    """General customs value of US imports for an HS code, recent months -> [(time, value)]."""
    if not enabled():
        return []
    params = {"get": "GEN_VAL_MO,I_COMMODITY", "I_COMMODITY": hs,
              "time": "from 2024-01", "key": os.environ["CENSUS_API_KEY"]}
    url = BASE + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"}), timeout=25) as r:
            rows = json.loads(r.read())
        hdr = rows[0]; ti, vi = hdr.index("time"), hdr.index("GEN_VAL_MO")
        series = sorted(([row[ti], float(row[vi])] for row in rows[1:] if row[vi] not in (None, "")), key=lambda x: x[0])
        return series[-months:]
    except Exception:
        return []
