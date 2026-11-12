"""NASA FIRMS — satellite wildfire thermal anomalies (owner: S6; used by the research app).
Set FIRMS_MAP_KEY (firms.modaps.eosdis.nasa.gov/api/map_key). The area CSV API takes the
key only as a PATH segment; core.http scrubs it from errors. [] when unconfigured or on failure.
"""
from __future__ import annotations

import csv
import io
import os
import re

from core import http

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCE = "firms"
# bbox = west,south,east,north
REGIONS = {"us_west": "-125,32,-114,43", "us_gulf": "-98,25,-88,32", "us_midwest": "-104,37,-87,49"}
_BBOX = re.compile(r"^-?\d{1,3}(\.\d+)?(,-?\d{1,3}(\.\d+)?){3}$")
_SRC = re.compile(r"^[A-Z0-9_]{3,40}$")


def enabled():
    return bool(os.environ.get("FIRMS_MAP_KEY"))


def fires(region="us_west", source="VIIRS_SNPP_NRT", days=2):
    if not enabled():
        return []
    area = REGIONS.get(region, region)
    if not _BBOX.match(str(area)) or not _SRC.match(str(source)):
        return []
    url = f"{BASE}/{os.environ['FIRMS_MAP_KEY']}/{source}/{area}/{max(1, min(int(days), 10))}"
    try:
        text = http.get_text(url, timeout=25, source=SOURCE)
        rows = list(csv.DictReader(io.StringIO(text)))
        return [{"lat": float(x.get("latitude", 0)), "lon": float(x.get("longitude", 0)),
                 "confidence": x.get("confidence"), "frp": x.get("frp"), "date": x.get("acq_date")}
                for x in rows]
    except Exception:
        return []


def count(region="us_west", days=2):
    return len(fires(region, days=days))
