"""BLS v2 — Producer Price Index (PPI) commodity series. Causal sensor: accelerating
PPI = input-cost / inflation pressure. Set BLS_API_KEY. POST JSON, registrationkey.
"""
from __future__ import annotations
import os, json, urllib.request

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

def series(series_id="WPUFD4", start=None, end=None):
    """WPUFD4 = PPI final demand. Returns [(period, value)] newest last."""
    body = {"seriesid": [series_id]}
    if os.environ.get("BLS_API_KEY"):
        body["registrationkey"] = os.environ["BLS_API_KEY"]
    if start: body["startyear"] = str(start)
    if end: body["endyear"] = str(end)
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "openterm/0.1"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=25).read())
        rows = d["Results"]["series"][0]["data"]
        pts = [(f"{r['year']}-{r['period']}", float(r["value"])) for r in rows]
        return list(reversed(pts))
    except Exception:
        return []
