"""Tropical cyclones (WX module). Keyless.

1. NOAA National Hurricane Center / Central Pacific Hurricane Center — Atlantic (AL),
   Eastern North Pacific (EP), Central North Pacific (CP):
     CurrentStorms.json     position, class, wind, pressure, motion, advisory number and time
     …_TRACK.kmz            the forecast points of THAT advisory (valid time, position, wind)
     …_CONE.kmz             the cone of uncertainty
     …_best_track.kmz       where the system has been (6-hourly, with wind)
     …_initialradii_….kmz   the 34 / 50 / 64 kt wind field at advisory time
     public advisory text   headline and the "NEXT ADVISORY" paragraph
   The KMZ links come from CurrentStorms.json itself and carry the advisory number, so
   position and geometry always belong to the same advisory. (NOAA's ArcGIS MapServer has
   the same geometry as GeoJSON but was a full advisory behind for more than two hours when
   this was written, 2026-11-12: it is not used.)
2. Other basins — Esri Living Atlas "Active Hurricanes" feature service, which relays the
   JTWC warnings (Western Pacific, Indian Ocean, Southern Hemisphere) next to NHC's.

One function per upstream call (`fetch_*`); every parser is pure and runs on a recorded file.

Units, verified on two advisories of CP042026 (text product, KMZ and the Esri relay):
`intensity` is in knots; `movementSpeed` is in MILES PER HOUR (advisory 24: JSON 18, text
"AT 18 MPH", forecast advisory 16 kt) — it is converted here.
"""
from __future__ import annotations

import calendar
import html as _html
import io
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import urlparse

from core import http
from core.errors import UpstreamError
from core.util import iso_epoch, num

SOURCE = "nhc"
HOST = "www.nhc.noaa.gov"
CURRENT_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
MPH_TO_KT = 0.868976
MAX_KML_BYTES = 8 * 1024 * 1024

NHC_BASINS = {"AL": "Atlantic", "EP": "Eastern North Pacific", "CP": "Central North Pacific"}
BASIN_NAMES = dict(NHC_BASINS, **{
    "WP": "Western North Pacific", "IO": "North Indian Ocean", "SH": "Southern Hemisphere",
    "SI": "South Indian Ocean", "SP": "South Pacific", "SL": "South Atlantic",
})
CLASS_NAMES = {
    "TD": "Tropical Depression", "TS": "Tropical Storm", "HU": "Hurricane", "MH": "Major Hurricane",
    "STD": "Subtropical Depression", "STS": "Subtropical Storm",
    "PTC": "Post-Tropical / Potential Tropical Cyclone", "PC": "Post-Tropical Cyclone",
    "TY": "Typhoon", "STY": "Super Typhoon", "TC": "Tropical Cyclone",
    "EX": "Extratropical Cyclone", "LO": "Remnant Low", "DB": "Disturbance", "WV": "Tropical Wave",
}
# UTC offsets (hours) of the zone abbreviations NHC / CPHC print in advisories.
ZONES = {"UTC": 0, "GMT": 0, "Z": 0, "AST": -4, "ADT": -3, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
         "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10, "HDT": -9,
         "SST": -11, "CHST": 10, "CVT": -1}
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


# ------------------------------------------------------------------ small helpers
def norm_lon(lon: float) -> float:
    """Any longitude (NHC files use −180.9 and 189.7 across the date line) -> [−180, 180)."""
    return round(((float(lon) + 180.0) % 360.0) - 180.0, 4)


def compass(deg) -> str | None:
    d = num(deg)
    return None if d is None else COMPASS[int((d % 360) / 22.5 + 0.5) % 16]


def wind_class(kt) -> str | None:
    """Class implied by the maximum sustained wind alone (used for track points, which carry no class)."""
    k = num(kt)
    if k is None:
        return None
    return "TD" if k < 34 else "TS" if k < 64 else "HU" if k < 96 else "MH"


def saffir_simpson(kt) -> int | None:
    k = num(kt)
    if k is None or k < 64:
        return None
    return 1 if k < 83 else 2 if k < 96 else 3 if k < 113 else 4 if k < 137 else 5


def _nhc_url(url, suffixes) -> str:
    """A link taken from CurrentStorms.json is fetched only if it points at NHC itself."""
    p = urlparse(str(url or ""))
    if p.scheme != "https" or p.netloc.lower() != HOST or not p.path.lower().endswith(suffixes) \
            or p.query or p.fragment or ".." in p.path:
        raise UpstreamError(SOURCE, None, "CurrentStorms.json links to an address that is not an NHC product file")
    return "https://" + HOST + p.path


_LOCAL = re.compile(r"(\d{1,2}):?(\d{2})\s*([AP]M)\s+([A-Za-z]{1,5})\s+(?:[A-Za-z]{3}\s+)?([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
_CLOCK = re.compile(r"(\d{1,2}):?(\d{2})\s*([AP]M)\s+([A-Za-z]{1,5})")


def _hour24(h: int, ampm: str) -> int:
    return (h % 12) + (12 if ampm.upper() == "PM" else 0)


def local_epoch(text) -> float | None:
    """'5:00 PM HST November 11, 2026' / '500 PM HST Wed Nov 11 2026' -> epoch s (None: unknown zone)."""
    m = _LOCAL.search(str(text or ""))
    if not m:
        return None
    hh, mi, ampm, zone, mon, day, year = m.groups()
    off, month = ZONES.get(zone.upper()), _MONTHS.get(mon[:3].lower())
    if off is None or month is None:
        return None
    try:
        return calendar.timegm((int(year), month, int(day), _hour24(int(hh), ampm), int(mi), 0)) - off * 3600.0
    except (ValueError, OverflowError):
        return None


def next_clock_epoch(text, after: float) -> float | None:
    """'1100 PM HST' -> the first time that clock reading occurs after `after` (epoch s)."""
    m = _CLOCK.search(str(text or ""))
    if not m or after is None:
        return None
    hh, mi, ampm, zone = m.groups()
    off = ZONES.get(zone.upper())
    if off is None:
        return None
    local = time.gmtime(after + off * 3600.0)
    t = calendar.timegm((local.tm_year, local.tm_mon, local.tm_mday, _hour24(int(hh), ampm), int(mi), 0)) - off * 3600.0
    while t <= after:
        t += 86400.0
    return t


def scheduled_next(after: float) -> float:
    """NHC issues full advisories at 03, 09, 15 and 21 UTC: the first of those after `after`."""
    day = int(after // 86400) * 86400
    for k in range(0, 9):
        t = day + (3 + 6 * k) * 3600
        if t > after + 60:
            return float(t)
    return float(day + 27 * 3600)


# ------------------------------------------------------------------ CurrentStorms.json
def fetch_current(timeout: float = 12):
    return http.get_json(CURRENT_URL, timeout=timeout, source=SOURCE)


def _link(block, key, suffixes):
    try:
        return _nhc_url((block or {}).get(key), suffixes)
    except UpstreamError:
        return None


def parse_current(payload) -> list[dict]:
    """CurrentStorms.json -> one dict per active system (no geometry yet)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("activeStorms"), list):
        raise UpstreamError(SOURCE, None, "CurrentStorms.json has an unexpected shape (no activeStorms list)")
    out = []
    for s in payload["activeStorms"]:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip().lower()
        lat, lon = num(s.get("latitudeNumeric")), num(s.get("longitudeNumeric"))
        if not re.match(r"^[a-z]{2}\d{6}$", sid) or lat is None or lon is None or abs(lat) > 90:
            continue                                    # nothing that could be placed on a map
        basin = sid[:2].upper()
        adv = s.get("publicAdvisory") if isinstance(s.get("publicAdvisory"), dict) else {}
        cls = str(s.get("classification") or "").strip().upper() or None
        wind = num(s.get("intensity"))
        mph = num(s.get("movementSpeed"))
        heading = num(s.get("movementDir"))
        if mph is None or mph <= 0:
            mph = None
        if heading is not None and not 0 <= heading <= 360:
            heading = None
        advisory_at = iso_epoch(adv.get("issuance")) or iso_epoch(s.get("lastUpdate"))
        adv_num = str(adv.get("advNum") or "").strip().lstrip("0") or None
        out.append({
            "id": sid, "name": str(s.get("name") or sid.upper()).strip(), "title": None,
            "cls": cls, "cls_name": CLASS_NAMES.get(cls or "", cls),
            "basin": basin, "basin_name": BASIN_NAMES.get(basin, basin), "bin": s.get("binNumber"),
            "lat": lat, "lon": norm_lon(lon),
            "wind_kt": wind, "gust_kt": None, "category": saffir_simpson(wind) if cls in ("HU", "MH") else None,
            "pressure_mb": num(s.get("pressure")),
            "heading_deg": heading if mph is not None else None,
            "heading_txt": compass(heading) if mph is not None else None,
            "speed_kt": round(mph * MPH_TO_KT) if mph is not None else None, "speed_mph": mph,
            "advisory_at": advisory_at, "advisory_num": adv_num,
            "source": "noaa-nhc",
            "links": {"advisory": _link(adv, "url", (".shtml",)),
                      "graphics": _link(s.get("forecastGraphics"), "url", (".shtml",))},
            "files": {"track": _link(s.get("forecastTrack"), "kmzFile", (".kmz",)),
                      "cone": _link(s.get("trackCone"), "kmzFile", (".kmz",)),
                      "best_track": _link(s.get("bestTrackGIS"), "kmzFile", (".kmz",)),
                      "radii": _link(s.get("initialWindExtent"), "kmzFile", (".kmz",))},
        })
    return out


# ------------------------------------------------------------------ KMZ products
def fetch_kmz(url: str, timeout: float = 12) -> bytes:
    return http.get_bytes(_nhc_url(url, (".kmz",)), timeout=timeout, source=SOURCE)


def fetch_advisory_text(url: str, timeout: float = 12) -> str:
    return http.get_text(_nhc_url(url, (".shtml",)), timeout=timeout, source=SOURCE)


def kml_root(raw: bytes):
    """KMZ bytes -> ElementTree root with namespaces removed (the files mix KML 2.1 / 2.2 and quote styles)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info = next((i for i in z.infolist() if i.filename.lower().endswith(".kml")), None)
            if info is None:
                raise UpstreamError(SOURCE, None, "KMZ without a KML document")
            if info.file_size > MAX_KML_BYTES:
                raise UpstreamError(SOURCE, None, "KML document larger than 8 MB, refused")
            text = z.read(info)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as e:
        raise UpstreamError(SOURCE, None, f"KMZ could not be opened ({type(e).__name__})") from None
    if b"<!DOCTYPE" in text or b"<!ENTITY" in text:
        raise UpstreamError(SOURCE, None, "KML with a document type declaration, refused")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise UpstreamError(SOURCE, None, f"KML is not well-formed ({e})") from None
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _coords(text) -> list[list[float]]:
    out = []
    for tok in str(text or "").split():
        p = tok.split(",")
        try:
            lon, lat = float(p[0]), float(p[1])
        except (IndexError, ValueError):
            continue
        if abs(lat) <= 90 and abs(lon) <= 540:
            out.append([norm_lon(lon), round(lat, 4)])
    return out


def _thin(ring: list, limit: int) -> list:
    """Drop repeated points and keep at most ~`limit` of them (first and last always)."""
    dedup = [p for i, p in enumerate(ring) if i == 0 or p != ring[i - 1]]
    if len(dedup) <= limit:
        return dedup
    step = -(-len(dedup) // limit)
    out = dedup[::step]
    if out[-1] != dedup[-1]:
        out.append(dedup[-1])
    return out


def _span(ring: list) -> float:
    """Rough extent in degrees (longitudes unwrapped), to recognise a polygon collapsed onto one point."""
    if not ring:
        return 0.0
    lons, prev, off = [], None, 0.0
    for lon, _ in ring:
        if prev is not None:
            off += -360.0 if lon - prev > 180 else 360.0 if lon - prev < -180 else 0.0
        prev = lon
        lons.append(lon + off)
    lats = [p[1] for p in ring]
    return max(max(lons) - min(lons), max(lats) - min(lats))


_STYLE_CLASS = {"d": "TD", "s": "TS", "h": "HU", "m": "MH", "l": "LO", "xd": "PTC", "xs": "PTC", "xh": "PTC", "xm": "PTC"}


def _cell(desc: str, label: str):
    m = re.search(label + r"\s*:?\s*([^<]+?)\s*<", desc or "", re.I)
    return m.group(1).strip() if m else None


def parse_track(root) -> dict:
    """…_TRACK.kmz -> {title, advisory_num, gust_kt, issued_text, points:[{tau_h, t, valid_text, lon, lat,
    wind_kt, gust_kt, cls}]}; points[0] is the advisory position (tau 0), the rest the forecast."""
    out = {"title": None, "advisory_num": None, "gust_kt": None, "issued_text": None, "issued_at": None, "points": []}
    for pm in root.iter("Placemark"):
        data = {}
        for d in pm.iter("Data"):
            v = d.find("value")
            data[d.get("name") or ""] = (v.text or "").strip() if v is not None and v.text else ""
        if data:
            out["title"] = out["title"] or data.get("storm") or None
            out["advisory_num"] = out["advisory_num"] or (data.get("advisoryNum") or "").lstrip("0") or None
            if data.get("maxGustKnots"):
                out["gust_kt"] = num(data.get("maxGustKnots"))
            if data.get("pubAdvTime"):
                out["issued_text"] = data["pubAdvTime"]
                out["issued_at"] = local_epoch(data["pubAdvTime"])
        point = pm.find("Point")
        if point is None:
            continue
        c = _coords(point.findtext("coordinates"))
        if not c:
            continue
        desc = pm.findtext("description") or ""
        m = re.search(r"(\d+)\s*hr\s+Forecast", desc, re.I)
        if m:
            tau = int(m.group(1))
        elif re.search(r"Advisory Information", desc, re.I):
            tau = 0
        else:
            continue
        valid = _cell(desc, r"Valid at")
        wind = num((re.search(r"Maximum Wind:\s*(\d+)\s*knots", desc, re.I) or [None, None])[1])
        gust = num((re.search(r"Wind Gusts:\s*(\d+)\s*knots", desc, re.I) or [None, None])[1])
        style = (pm.findtext("styleUrl") or "").strip().lstrip("#").replace("_point", "")
        out["points"].append({
            "tau_h": tau, "t": local_epoch(valid), "valid_text": valid,
            "lon": c[0][0], "lat": c[0][1], "wind_kt": wind, "gust_kt": gust,
            "cls": _STYLE_CLASS.get(style) or wind_class(wind),
        })
        if tau == 0 and gust is not None and out["gust_kt"] is None:
            out["gust_kt"] = gust
    out["points"].sort(key=lambda p: p["tau_h"])
    return out


def parse_cone(root) -> list[list]:
    """…_CONE.kmz -> [ring, …], ring = [[lon, lat], …] (longitudes in [−180, 180); the map unwraps them)."""
    rings = []
    for poly in root.iter("Polygon"):
        outer = poly.find("outerBoundaryIs")
        ring = _coords(outer.findtext(".//coordinates") if outer is not None else poly.findtext(".//coordinates"))
        if len(ring) >= 4 and _span(ring) >= 0.02:
            rings.append([[round(p[0], 3), round(p[1], 3)] for p in _thin(ring, 500)])
    return rings


def parse_best_track(root) -> list[dict]:
    """…_best_track.kmz -> [{t, lon, lat, wind_kt, pressure_mb, cls}] ascending in time."""
    out = []
    for pm in root.iter("Placemark"):
        dtg = (pm.findtext("atcfdtg") or "").strip()
        if not re.match(r"^\d{10}$", dtg):
            continue
        try:
            t = calendar.timegm((int(dtg[:4]), int(dtg[4:6]), int(dtg[6:8]), int(dtg[8:10]), 0, 0))
        except (ValueError, OverflowError):
            continue
        lat, lon = num(pm.findtext("lat")), num(pm.findtext("lon"))
        if lat is None or lon is None:
            c = _coords(pm.findtext(".//coordinates"))
            if not c:
                continue
            lon, lat = c[0]
        pres = num(pm.findtext("minSeaLevelPres"))
        out.append({"t": float(t), "lon": norm_lon(lon), "lat": round(lat, 4), "wind_kt": num(pm.findtext("intensity")),
                    "pressure_mb": pres if pres else None, "cls": (pm.findtext("stormType") or "").strip().upper() or None})
    out.sort(key=lambda p: p["t"])
    return out


def parse_radii(root) -> list[dict]:
    """…_initialradii_….kmz -> [{kt: 34|50|64, ring}] (a field collapsed onto the centre is dropped)."""
    out = []
    for pm in root.iter("Placemark"):
        kt = num(pm.findtext("name"))
        if kt not in (34.0, 50.0, 64.0):
            continue
        for poly in pm.iter("Polygon"):
            ring = _coords(poly.findtext(".//coordinates"))
            if len(ring) >= 4 and _span(ring) >= 0.02:
                out.append({"kt": int(kt), "ring": [[round(p[0], 3), round(p[1], 3)] for p in _thin(ring, 120)]})
    out.sort(key=lambda r: r["kt"])
    return out


def parse_public_advisory(page: str) -> dict:
    """The public advisory (TCP) page -> {headline:[…], next_text, final, next_complete, next_intermediate}.
    The two clock strings are as printed ('1100 PM HST'); see next_clock_epoch."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", page or "", re.S | re.I)
    text = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1) if m else (page or ""))).replace("\r", "")
    head = text.split("SUMMARY OF", 1)[0]
    headline = [re.sub(r"\s+", " ", h).strip() for h in re.findall(r"^\.\.\.(.+?)\.\.\.[ \t]*$", head, re.S | re.M)]
    nxt = None
    m = re.search(r"NEXT ADVISORY\s*\n-+\s*\n(.*?)(?:\n\s*\$\$|\Z)", text, re.S)
    if m:
        nxt = re.sub(r"\s+", " ", m.group(1)).strip() or None
    comp = re.search(r"Next complete advisory at\s+(\d{3,4}\s*[AP]M\s+[A-Za-z]{1,5})", nxt or "", re.I)
    inter = re.search(r"Next intermediate advisory at\s+(\d{3,4}\s*[AP]M\s+[A-Za-z]{1,5})", nxt or "", re.I)
    number = re.search(r"Advisory Number\s+0*(\d+[A-Za-z]?)", text)
    return {
        "advisory_num": number.group(1).upper() if number else None,
        "headline": [h for h in headline if h][:4],
        "next_text": nxt,
        "final": bool(nxt and re.search(r"\blast\b.*\badvisory\b", nxt, re.I)),
        "next_complete": comp.group(1) if comp else None,
        "next_intermediate": inter.group(1) if inter else None,
    }


# ------------------------------------------------------------------ other basins (Esri relay of JTWC + NHC)
ESRI_SOURCE = "esri"
ESRI_BASE = ("https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services/"
             "Active_Hurricanes_v1/FeatureServer")
ESRI_LAYERS = {"forecast": 0, "observed": 1, "cone": 4}
_TCDVLP = {"tropical depression": "TD", "tropical storm": "TS", "hurricane": "HU", "major hurricane": "MH",
           "post-tropical cyclone": "PC", "subtropical storm": "STS", "subtropical depression": "STD",
           "potential tropical cyclone": "PTC", "tropical cyclone": "TC"}


def fetch_esri(layer: str, timeout: float = 12):
    """One layer of the feature service as GeoJSON. The query is fixed: nothing of it comes from a client."""
    url = f"{ESRI_BASE}/{ESRI_LAYERS[layer]}/query?" + http.qs({"where": "1=1", "outFields": "*", "f": "geojson"})
    return http.get_json(url, timeout=timeout, source=ESRI_SOURCE)


def _features(fc) -> list:
    if not isinstance(fc, dict) or not isinstance(fc.get("features"), list):
        raise UpstreamError(ESRI_SOURCE, None, "the Active Hurricanes service did not return a feature list")
    return [f for f in fc["features"] if isinstance(f, dict) and isinstance(f.get("properties"), dict)]


def _valid_epoch(adv_s, validtime) -> float | None:
    """'13/1200' (day of month / HHMM UTC) -> epoch s, the first such moment from a day before the advisory."""
    m = re.match(r"^\s*(\d{1,2})/(\d{2})(\d{2})\s*$", str(validtime or ""))
    if not m or adv_s is None:
        return None
    dd, hh, mi = int(m.group(1)), int(m.group(2)), int(m.group(3))
    for k in range(-1, 12):
        day = time.gmtime(adv_s + k * 86400)
        if day.tm_mday == dd:
            return float(calendar.timegm((day.tm_year, day.tm_mon, dd, hh, mi, 0)))
    return None


def _geo_point(f):
    g = f.get("geometry") or {}
    c = g.get("coordinates") if g.get("type") == "Point" else None
    if isinstance(c, list) and len(c) >= 2 and num(c[0]) is not None and num(c[1]) is not None and abs(c[1]) <= 90:
        return norm_lon(c[0]), round(float(c[1]), 4)
    return None


def _missing(v):
    v = num(v)
    return None if v is None or v <= 0 or v >= 9999 else v


def parse_esri(forecast_fc, observed_fc=None, cone_fc=None) -> list[dict]:
    """The three layers -> storms in the shape of parse_current + geometry (every basin the service lists)."""
    groups: dict = {}
    for f in _features(forecast_fc):
        p, pt = f["properties"], _geo_point(f)
        name, basin = str(p.get("STORMNAME") or "").strip(), str(p.get("BASIN") or "").strip().upper()
        if not name or not basin or pt is None:
            continue
        adv = num(p.get("ADVDATE"))
        adv = adv / 1000.0 if adv else None
        groups.setdefault((basin, name), []).append({
            "p": p, "lon": pt[0], "lat": pt[1], "adv": adv, "t": _valid_epoch(adv, p.get("VALIDTIME")),
            "tau": num(p.get("TAU")) or 0.0})
    past_by, id_by = {}, {}
    for f in (_features(observed_fc) if observed_fc is not None else []):
        p, pt = f["properties"], _geo_point(f)
        name, basin = str(p.get("STORMNAME") or "").strip(), str(p.get("BASIN") or "").strip().upper()
        t = num(p.get("DTG"))
        if not name or pt is None or not t:
            continue
        past_by.setdefault((basin, name), []).append([pt[0], pt[1], t / 1000.0, num(p.get("INTENSITY"))])
        sid = str(p.get("STORMID") or "").strip().lower()
        if re.match(r"^[a-z]{2}\d{6}$", sid):
            id_by[(basin, name)] = sid
    cone_by = {}
    for f in (_features(cone_fc) if cone_fc is not None else []):
        p, g = f["properties"], f.get("geometry") or {}
        key = (str(p.get("BASIN") or "").strip().upper(), str(p.get("STORMNAME") or "").strip())
        polys = g.get("coordinates") if g.get("type") == "MultiPolygon" else [g.get("coordinates")] if g.get("type") == "Polygon" else []
        for poly in polys or []:
            ring = [[norm_lon(c[0]), round(float(c[1]), 4)] for c in (poly[0] if poly else [])
                    if isinstance(c, list) and len(c) >= 2 and num(c[0]) is not None and num(c[1]) is not None]
            if len(ring) >= 4 and _span(ring) >= 0.02:
                cone_by.setdefault(key, []).append([[round(q[0], 3), round(q[1], 3)] for q in _thin(ring, 500)])
    out = []
    for (basin, name), rows in groups.items():
        rows.sort(key=lambda r: (r["tau"], r["t"] if r["t"] is not None else 0.0))
        cur, p = rows[0], rows[0]["p"]
        wind = _missing(p.get("MAXWIND"))
        speed = _missing(p.get("TCSPD"))
        heading = num(p.get("TCDIR")) if speed is not None else None
        if heading is not None and not 0 <= heading <= 360:
            heading = None
        cls = _TCDVLP.get(str(p.get("TCDVLP") or "").strip().lower()) or wind_class(wind)
        local_name = str(p.get("ITCDVLP") or p.get("TCDVLP") or "").strip() or CLASS_NAMES.get(cls or "", cls)
        year = time.gmtime(cur["adv"]).tm_year if cur["adv"] else time.gmtime().tm_year
        sid = id_by.get((basin, name)) or f"{basin.lower()}{int(num(p.get('STORMNUM')) or 0):02d}{year}"
        past = sorted(past_by.get((basin, name), []), key=lambda r: r[2])
        out.append({
            "id": sid, "name": name, "title": f"{local_name} {name}".strip(),
            "cls": cls, "cls_name": local_name,
            "basin": basin, "basin_name": BASIN_NAMES.get(basin, basin), "bin": None,
            "lat": cur["lat"], "lon": cur["lon"],
            "wind_kt": wind, "gust_kt": _missing(p.get("GUST")),
            "category": saffir_simpson(wind) if cls in ("HU", "MH") else None,
            "pressure_mb": _missing(p.get("MSLP")),
            "heading_deg": heading, "heading_txt": compass(heading), "speed_kt": speed, "speed_mph": None,
            "advisory_at": cur["adv"], "advisory_num": str(p.get("ADVISNUM") or "").strip().lstrip("0") or None,
            "position_at": cur["t"],
            "source": "esri-active-hurricanes",
            "links": {"advisory": None, "graphics": None}, "files": {},
            "past": past,
            "forecast": [[r["lon"], r["lat"], r["t"], _missing(r["p"].get("MAXWIND"))] for r in rows[1:]],
            "forecast_detail": [{"tau_h": (round((r["t"] - cur["t"]) / 3600.0) if r["t"] and cur["t"] else None),
                                 "gust_kt": _missing(r["p"].get("GUST")),
                                 "cls": _TCDVLP.get(str(r["p"].get("TCDVLP") or "").strip().lower()),
                                 "valid_text": str(r["p"].get("FLDATELBL") or "").strip() or None} for r in rows[1:]],
            "cone": cone_by.get((basin, name), []), "wind_radii": [],
        })
    out.sort(key=lambda s: (s["basin"], s["name"]))
    return out
