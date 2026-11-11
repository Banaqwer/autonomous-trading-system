"""IMF / Eurostat / OECD macro — no key. Minimal fetchers returning tidy rows.
"""
from __future__ import annotations
import json, urllib.request
def _j(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"openterm/0.1"}), timeout=30) as r:
        return json.loads(r.read())

def imf(indicator="NGDP_RPCH", country="USA"):
    """IMF DataMapper: % change real GDP by default."""
    d = _j(f"https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}")
    vals = (d.get("values", {}).get(indicator, {}) or {}).get(country, {})
    return [{"date": k, "value": v} for k, v in sorted(vals.items())]

def eurostat(dataset="prc_hicp_manr", params="geo=EA&coicop=CP00"):
    """Eurostat JSON-stat (euro-area HICP inflation by default). Returns raw value map."""
    url = (f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
           f"{dataset}?format=JSON&{params}")
    d = _j(url)
    idx = d.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    vals = d.get("value", {})
    rev = {v: k for k, v in idx.items()}
    return [{"date": rev.get(int(i)), "value": v} for i, v in vals.items()]

def oecd(flow="OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA", key="all", last=8):
    """OECD SDMX-JSON (generic; returns observation count). Kept minimal — formats vary."""
    url = f"https://sdmx.oecd.org/public/rest/data/{flow}/{key}?format=jsondata&lastNObservations={last}"
    try:
        d = _j(url); n = len(d.get("data", {}).get("dataSets", [{}])[0].get("series", {}))
        return {"ok": True, "series": n}
    except Exception as e:
        return {"ok": False, "note": "OECD SDMX format varies by dataflow", "err": str(e)[:120]}
