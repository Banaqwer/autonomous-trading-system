"""FRED (Federal Reserve Economic Data) — macro series for the ECO module.
Free key: set FRED_API_KEY (fred.stlouisfed.org/docs/api/api_key.html).
"""
from __future__ import annotations
import os, json, urllib.request

BASE = "https://api.stlouisfed.org/fred"

def series(series_id="DCOILWTICO", limit=120):
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set")
    url = (f"{BASE}/series/observations?series_id={series_id}"
           f"&api_key={key}&file_type=json&sort_order=desc&limit={limit}")
    with urllib.request.urlopen(url, timeout=20) as r:
        obs = json.loads(r.read())["observations"]
    return [{"date": o["date"], "value": None if o["value"] == "." else float(o["value"])}
            for o in reversed(obs)]
