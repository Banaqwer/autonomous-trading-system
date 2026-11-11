"""World Bank Open Data — macro indicators by country. No key.
e.g. NY.GDP.MKTP.CD GDP, FP.CPI.TOTL.ZG inflation, SL.UEM.TOTL.ZS unemployment.
"""
from __future__ import annotations
import json, urllib.request

def indicator(country="US", code="NY.GDP.MKTP.CD", per_page=60):
    url = f"https://api.worldbank.org/v2/country/{country}/indicator/{code}?format=json&per_page={per_page}"
    with urllib.request.urlopen(url, timeout=25) as r:
        d = json.loads(r.read())
    rows = d[1] if isinstance(d, list) and len(d) > 1 and d[1] else []
    return [{"date": x["date"], "value": x["value"]} for x in rows if x.get("value") is not None]
