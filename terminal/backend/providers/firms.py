"""NASA FIRMS — satellite wildfire thermal anomalies (causal sensor).
Set FIRMS_MAP_KEY (firms.modaps.eosdis.nasa.gov/api/map_key). Area CSV API.
"""
from __future__ import annotations
import os, csv, io, urllib.request

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# bbox = west,south,east,north
REGIONS = {"us_west": "-125,32,-114,43", "us_gulf": "-98,25,-88,32", "us_midwest": "-104,37,-87,49"}

def enabled():
    return bool(os.environ.get("FIRMS_MAP_KEY"))

def fires(region="us_west", source="VIIRS_SNPP_NRT", days=2):
    if not enabled():
        return []
    area = REGIONS.get(region, region)
    url = f"{BASE}/{os.environ['FIRMS_MAP_KEY']}/{source}/{area}/{days}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"}), timeout=25) as r:
            text = r.read().decode("utf-8", "ignore")
        rows = list(csv.DictReader(io.StringIO(text)))
        return [{"lat": float(x.get("latitude", 0)), "lon": float(x.get("longitude", 0)),
                 "confidence": x.get("confidence"), "frp": x.get("frp"), "date": x.get("acq_date")}
                for x in rows]
    except Exception:
        return []

def count(region="us_west", days=2):
    return len(fires(region, days=days))
