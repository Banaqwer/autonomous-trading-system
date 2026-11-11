"""NOAA National Hurricane Center — active tropical systems (WX module).
No key. CurrentStorms.json gives position/intensity/motion; cone/track are KMZ.
"""
from __future__ import annotations
import urllib.request, json

URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

def active_storms():
    req = urllib.request.Request(URL, headers={"User-Agent": "openterm/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    out = []
    for s in data.get("activeStorms", []):
        out.append({
            "id": s.get("id"), "name": s.get("name"),
            "classification": s.get("classification"),
            "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
            "intensity_kt": _num(s.get("intensity")), "pressure_mb": _num(s.get("pressure")),
            "heading_deg": s.get("movementDir"), "speed_kt": s.get("movementSpeed"),
            "updated": s.get("lastUpdate"),
            "cone_kmz": (s.get("trackCone") or {}).get("kmzFile"),
            "track_kmz": (s.get("forecastTrack") or {}).get("kmzFile"),
        })
    return out

def _num(v):
    try: return float(v)
    except (TypeError, ValueError): return None
