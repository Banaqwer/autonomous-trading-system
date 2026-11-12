"""US Census Bureau international trade (owner: S6; used by the research app). Monthly
imports by HS code. Set CENSUS_API_KEY (api.census.gov/data/key_signup.html); the API takes
the key only as the `key` query parameter. Returns [] when unconfigured or on any failure.
"""
from __future__ import annotations

import os
import re

from core import http

BASE = "https://api.census.gov/data/timeseries/intltrade/imports/hs"
SOURCE = "census"


def enabled():
    return bool(os.environ.get("CENSUS_API_KEY"))


def imports_hs(hs="2709", months=8):
    """General customs value of US imports for an HS code, recent months -> [(time, value)]."""
    if not enabled() or not re.match(r"^\d{2,10}$", str(hs)):
        return []
    params = {"get": "GEN_VAL_MO,I_COMMODITY", "I_COMMODITY": str(hs),
              "time": "from 2024-01", "key": os.environ["CENSUS_API_KEY"]}
    try:
        rows = http.get_json(BASE + "?" + http.qs(params), timeout=25, source=SOURCE)
        hdr = rows[0]
        ti, vi = hdr.index("time"), hdr.index("GEN_VAL_MO")
        series = sorted(([row[ti], float(row[vi])] for row in rows[1:] if row[vi] not in (None, "")), key=lambda x: x[0])
        return series[-max(1, int(months)):]
    except Exception:
        return []
