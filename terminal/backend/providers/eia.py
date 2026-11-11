"""EIA (US Energy Information Administration) — energy fundamentals (WX/oil scenario).
Free key: EIA_API_KEY (eia.gov/opendata/register.php). v2 series endpoint.
Useful series: PET.WCESTUS1.W weekly crude stocks; NG.NW2_EPG0_SWO_R48_BCF.W nat-gas storage;
PET.WCRFPUS2.W refinery runs.
"""
from __future__ import annotations
import os, json, urllib.request

def series(series_id="PET.WCESTUS1.W", length=52):
    key = os.environ.get("EIA_API_KEY")
    if not key:
        raise RuntimeError("EIA_API_KEY not set — free at eia.gov/opendata/register.php")
    url = f"https://api.eia.gov/v2/seriesid/{series_id}?api_key={key}&length={length}"
    with urllib.request.urlopen(url, timeout=25) as r:
        d = json.loads(r.read())["response"]["data"]
    return [{"date": x.get("period"), "value": x.get("value")} for x in d]
