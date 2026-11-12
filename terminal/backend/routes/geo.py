"""WEATHER slice (S4): tropical cyclones, marine observations, cloud imagery — everything the WX module draws.

    GET /api/storms              active tropical cyclones: NHC / CPHC (Atlantic, East and Central Pacific) with the
                                 advisory's own forecast track, cone, wind field and past track; other basins from
                                 the Esri relay of JTWC, each storm with its source. `checked` says which basins
                                 were really asked; an empty `storms` list is a valid answer.
    GET /api/wx/obs?region=      measured marine observations (NOAA NDBC buoys and coastal stations), and for every
                                 energy reference point the nearest station that reports wind
    GET /api/wx/model            MODEL values (Open-Meteo) for the reference points no station covers; cached hard
    GET /api/wx/hubs             energy-infrastructure reference points (static reference data, labelled as such)
    GET /api/satellite/layers    the GIBS imagery layers that work, with the time that really has imagery
    GET /api/satellite/tile      same-origin tile proxy for those layers (whitelist + bounded cache; the map canvas
                                 stays readable because nothing is loaded from another origin)

Retired with the rewrite: /api/weather, /api/marine (one uncached Open-Meteo call per request with client
coordinates in the URL) and /api/satellite (a tile template that was HTTP 400 for half of its layers).

Every upstream call goes through core.cache (single-flight, TTL, stale-on-error) and core.http; tiles go
through the bounded providers.gibs.TileStore. No parameter of a client ever reaches an upstream URL unvalidated.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Query
from fastapi.responses import Response

from core import cache
from core.cache import cached
from core.errors import ApiError, BadRequest, UpstreamError, describe
from providers import gibs, ndbc, nhc, openmeteo

router = APIRouter()

STORMS_TTL = 180            # CurrentStorms.json: advisories are 3- to 6-hourly; the page polls every 300 s
GEOMETRY_TTL = 6 * 3600     # the files of one advisory never change
ESRI_TTL = 600
OBS_TTL = 300               # latest_obs.txt is rewritten every few minutes; the page polls every 300 s
STATIONS_TTL = 86400
MODEL_TTL = 3600            # Open-Meteo: cached hard (it answers 429 when an IP has used its daily allowance)
MODEL_ERROR_TTL = 900
LAYERS_TTL = 300            # geostationary imagery advances every 10 minutes
OBS_MAX_AGE = 6 * 3600      # an observation older than this is not "the weather now": dropped
HUB_OBS_KM = 150.0          # a station this close to a reference point counts as its observation
EARTH_KM = 6371.0088
KM_PER_NM = 1.852

# ------------------------------------------------------------------ reference points
# Static REFERENCE data (not a feed): where the oil and LNG infrastructure is that a storm can shut.
# lat / lon are the facility or the narrowest part of the strait. Each was checked against two independent
# sources (see `verified`); `tol_km` is how far the two sources disagree at most.
HUBS = [
    {"id": "houston", "name": "Houston Ship Channel", "kind": "refining", "lat": 29.73, "lon": -95.13,
     "what": "Largest US refining and petrochemical complex (Baytown, Deer Park, Pasadena)"},
    {"id": "texas_city", "name": "Texas City / Galveston Bay", "kind": "refining", "lat": 29.38, "lon": -94.90,
     "what": "Galveston Bay refinery and the Texas City terminals"},
    {"id": "port_arthur", "name": "Port Arthur / Beaumont", "kind": "refining", "lat": 29.89, "lon": -93.96,
     "what": "Motiva Port Arthur (largest US refinery), Valero, ExxonMobil Beaumont"},
    {"id": "sabine_pass", "name": "Sabine Pass LNG", "kind": "lng", "lat": 29.74, "lon": -93.87,
     "what": "LNG export terminal (Cheniere), Sabine Pass ship channel"},
    {"id": "lake_charles", "name": "Lake Charles / Cameron LNG", "kind": "lng", "lat": 30.04, "lon": -93.33,
     "what": "Cameron LNG (Hackberry) and the Lake Charles refineries on the Calcasieu channel"},
    {"id": "freeport", "name": "Freeport LNG", "kind": "lng", "lat": 28.94, "lon": -95.31,
     "what": "LNG export terminal on Quintana Island; Freeport crude terminals"},
    {"id": "corpus_christi", "name": "Corpus Christi", "kind": "refining", "lat": 27.82, "lon": -97.40,
     "what": "Largest US crude-export port; refineries; Corpus Christi LNG"},
    {"id": "loop", "name": "LOOP (offshore oil port)", "kind": "port", "lat": 28.885, "lon": -90.025,
     "what": "Louisiana Offshore Oil Port marine terminal: the only US deep-water port for VLCCs"},
    {"id": "mississippi_river", "name": "Mississippi River refining", "kind": "refining", "lat": 30.06, "lon": -90.62,
     "what": "Garyville, Norco, Convent, St. James: the river corridor between Baton Rouge and New Orleans"},
    {"id": "pascagoula", "name": "Pascagoula", "kind": "refining", "lat": 30.34, "lon": -88.49,
     "what": "Chevron Pascagoula refinery"},
    {"id": "cushing", "name": "Cushing, Oklahoma", "kind": "storage", "lat": 35.98, "lon": -96.76,
     "what": "WTI delivery point and the largest US crude storage hub (inland)"},
    {"id": "ny_harbor", "name": "New York Harbor", "kind": "refining", "lat": 40.64, "lon": -74.21,
     "what": "Bayway refinery and the NYH product terminals: delivery point of RBOB and heating-oil futures"},
    {"id": "delaware_river", "name": "Delaware River refining", "kind": "refining", "lat": 39.82, "lon": -75.40,
     "what": "Trainer, Paulsboro, Delaware City: the Philadelphia-area refineries"},
    {"id": "cove_point", "name": "Cove Point LNG", "kind": "lng", "lat": 38.39, "lon": -76.41,
     "what": "LNG export terminal on Chesapeake Bay"},
    {"id": "elba_island", "name": "Elba Island LNG", "kind": "lng", "lat": 32.08, "lon": -81.00,
     "what": "LNG export terminal on the Savannah River"},
    {"id": "hormuz", "name": "Strait of Hormuz", "kind": "chokepoint", "lat": 26.57, "lon": 56.25,
     "what": "About a fifth of the world's oil and LNG trade passes here"},
    {"id": "suez", "name": "Suez Canal", "kind": "chokepoint", "lat": 30.58, "lon": 32.27,
     "what": "Red Sea – Mediterranean; with the SUMED pipeline the main Gulf-to-Europe route"},
    {"id": "bab_el_mandeb", "name": "Bab-el-Mandeb", "kind": "chokepoint", "lat": 12.58, "lon": 43.33,
     "what": "Strait between the Red Sea and the Gulf of Aden"},
    {"id": "malacca", "name": "Strait of Malacca", "kind": "chokepoint", "lat": 2.50, "lon": 101.20,
     "what": "Shortest route from the Gulf to East Asia; narrowest in the Singapore Strait"},
    {"id": "panama", "name": "Panama Canal", "kind": "chokepoint", "lat": 9.08, "lon": -79.68,
     "what": "US Gulf – Pacific route for LPG, LNG and products"},
]
HUB_KINDS = {"refining": "Refining", "lng": "LNG export", "port": "Oil port", "storage": "Storage hub",
             "chokepoint": "Chokepoint"}
HUB_VERIFIED = ("Coordinates compared with two independent sources per point "
                "(see terminal/DEV_NOTES or the S4 hand-over for the table)")

# First match wins (the Caribbean box overlaps the south of the Gulf box).
REGIONS = [
    {"id": "carib", "name": "Caribbean", "bbox": [-88.5, 7.0, -59.0, 21.6]},
    {"id": "gulf", "name": "Gulf of Mexico", "bbox": [-98.0, 18.0, -81.5, 31.0]},
    {"id": "east", "name": "US East Coast", "bbox": [-81.5, 24.4, -60.0, 46.0]},
    {"id": "west", "name": "US West Coast", "bbox": [-130.0, 30.0, -116.0, 49.5]},
    {"id": "hawaii", "name": "Hawaii / C Pacific", "bbox": [-180.0, 0.0, -140.0, 32.0]},
]
REGION_IDS = ("all",) + tuple(r["id"] for r in REGIONS) + ("other",)

TILES = gibs.TileStore()


# ------------------------------------------------------------------ great-circle geometry (pure)
def gc_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km (haversine, mean Earth radius)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _vec(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _angle(a, b) -> float:
    c = _cross(a, b)
    return math.atan2(math.sqrt(_dot(c, c)), _dot(a, b))


def segment_km(lat, lon, a, b):
    """Distance in km from (lat, lon) to the great-circle segment a -> b (each [lon, lat, …]) and how far along
    the segment (0..1) the nearest point lies."""
    p, va, vb = _vec(lat, lon), _vec(a[1], a[0]), _vec(b[1], b[0])
    n = _cross(va, vb)
    nn = math.sqrt(_dot(n, n))
    if nn > 1e-12:
        n = (n[0] / nn, n[1] / nn, n[2] / nn)
        d = _dot(p, n)
        c = (p[0] - d * n[0], p[1] - d * n[1], p[2] - d * n[2])          # p dropped onto the great circle
        cn = math.sqrt(_dot(c, c))
        if cn > 1e-12 and _dot(_cross(va, c), n) >= 0 and _dot(_cross(c, vb), n) >= 0:
            c = (c[0] / cn, c[1] / cn, c[2] / cn)
            total = _angle(va, vb)
            return EARTH_KM * abs(math.asin(max(-1.0, min(1.0, d)))), (_angle(va, c) / total if total else 0.0)
    da, db = EARTH_KM * _angle(p, va), EARTH_KM * _angle(p, vb)
    return (da, 0.0) if da <= db else (db, 1.0)


def track_km(lat, lon, track):
    """Nearest approach of a track [[lon, lat, t, …], …] to a point: (km, time of that approach or None)."""
    pts = [q for q in track if q and q[0] is not None and q[1] is not None]
    if not pts:
        return None, None
    best, when = gc_km(lat, lon, pts[0][1], pts[0][0]), pts[0][2] if len(pts[0]) > 2 else None
    for a, b in zip(pts, pts[1:]):
        d, f = segment_km(lat, lon, a, b)
        if d < best:
            ta, tb = (a[2] if len(a) > 2 else None), (b[2] if len(b) > 2 else None)
            best = d
            when = ta + f * (tb - ta) if ta is not None and tb is not None else (ta if f < 0.5 else tb)
    return best, when


def nearest_hubs(storm: dict, limit: int = 4) -> list[dict]:
    """DERIVED: great-circle distance from the storm's forecast track (advisory position + forecast points) to the
    reference points; the nearest `limit`, with the time of the closest approach when the forecast gives one."""
    track = [[storm["lon"], storm["lat"], storm.get("advisory_at")]] + [list(p) for p in storm.get("forecast") or []]
    rows = []
    for h in HUBS:
        km, when = track_km(h["lat"], h["lon"], track)
        if km is None:
            continue
        rows.append({"id": h["id"], "name": h["name"], "kind": h["kind"], "km": round(km), "nm": round(km / KM_PER_NM),
                     "at": when if len(track) > 1 else None})
    rows.sort(key=lambda r: r["km"])
    return rows[:limit]


# ------------------------------------------------------------------ storms
def _part(key: str, ttl: float, fn):
    """One advisory product through the cache -> (value, None) or (None, error dict)."""
    try:
        return cache.fetch(key, ttl, fn).value, None
    except ApiError as e:
        return None, describe(e)


def _nhc_storm(s: dict) -> dict:
    """CurrentStorms row + the files of the same advisory -> the storm as the page gets it."""
    sid, adv = s["id"], s.get("advisory_num") or "0"
    files = s.pop("files", {}) or {}
    jobs = {}
    for kind, parse in (("track", nhc.parse_track), ("cone", nhc.parse_cone),
                        ("best_track", nhc.parse_best_track), ("radii", nhc.parse_radii)):
        url = files.get(kind)
        if url:
            jobs[kind] = (f"nhc:{kind}:{sid}:{adv}", GEOMETRY_TTL,
                          (lambda u=url, p=parse: p(nhc.kml_root(nhc.fetch_kmz(u)))))
    text_url = (s.get("links") or {}).get("advisory")
    if text_url:
        def advisory_text(u=text_url, want=adv):
            got = nhc.parse_public_advisory(nhc.fetch_advisory_text(u))
            if got.get("advisory_num") and got["advisory_num"] != want:
                raise UpstreamError("nhc", None, f"the advisory text on file is number {got['advisory_num']}, "
                                                 f"CurrentStorms.json says {want}")
            return got
        jobs["text"] = (f"nhc:text:{sid}:{adv}", GEOMETRY_TTL, advisory_text)
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {kind: pool.submit(_part, *job) for kind, job in jobs.items()}
        got = {kind: f.result() for kind, f in futures.items()}
    errors = [{"what": kind, **err} for kind, (_, err) in got.items() if err]
    track = (got.get("track") or (None, None))[0]
    if track and track.get("advisory_num") and track["advisory_num"] != adv:
        errors.append({"what": "track", "code": "upstream_error", "source": "nhc",
                       "message": f"the track file is for advisory {track['advisory_num']}, not {adv}"})
        track = None
    points = [p for p in (track or {}).get("points", []) if p["tau_h"] > 0]
    past = (got.get("best_track") or (None, None))[0] or []
    text = (got.get("text") or (None, None))[0] or {}
    s["title"] = (track or {}).get("title") or (f"{s['cls_name']} {s['name']}" if s.get("cls_name") else s["name"])
    s["gust_kt"] = (track or {}).get("gust_kt")
    s["position_at"] = s.get("advisory_at")
    s["past"] = [[p["lon"], p["lat"], p["t"], p["wind_kt"]] for p in past]
    s["forecast"] = [[p["lon"], p["lat"], p["t"], p["wind_kt"]] for p in points]
    s["forecast_detail"] = [{"tau_h": p["tau_h"], "gust_kt": p["gust_kt"], "cls": p["cls"], "valid_text": p["valid_text"]}
                            for p in points]
    s["cone"] = (got.get("cone") or (None, None))[0] or []
    s["wind_radii"] = (got.get("radii") or (None, None))[0] or []
    s["headline"] = text.get("headline") or []
    s["next_advisory"] = _next_advisory(s.get("advisory_at"), text)
    s["geometry_source"] = "NHC KMZ products of advisory " + adv
    s["errors"] = errors
    return s


def _next_advisory(advisory_at, text: dict) -> dict | None:
    if advisory_at is None:
        return None
    if text.get("final"):
        return {"at": None, "final": True, "basis": "advisory text", "text": text.get("next_text")}
    at = nhc.next_clock_epoch(text.get("next_complete"), advisory_at)
    if at is not None:
        return {"at": at, "final": False, "basis": "advisory text", "text": text.get("next_text"),
                "intermediate_at": nhc.next_clock_epoch(text.get("next_intermediate"), advisory_at)}
    return {"at": nhc.scheduled_next(advisory_at), "final": False, "basis": "schedule",
            "text": "NHC issues full advisories at 03, 09, 15 and 21 UTC (derived from that schedule)"}


def _esri_storms() -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut = {k: pool.submit(nhc.fetch_esri, k) for k in ("forecast", "observed", "cone")}
        forecast = fut["forecast"].result()
        extra = {}
        for k in ("observed", "cone"):
            try:
                extra[k] = fut[k].result()
            except ApiError:
                extra[k] = None                      # the positions are the product; tracks and cones are extras
    return {"as_of": time.time(), "storms": nhc.parse_esri(forecast, extra["observed"], extra["cone"])}


def _load_storms() -> dict:
    now = time.time()
    current = nhc.parse_current(nhc.fetch_current())           # a failure here fails the route (stale or 502)
    storms = [_nhc_storm(s) for s in current]
    checked = [{"basin": b, "name": n, "source": "noaa-nhc", "ok": True} for b, n in nhc.NHC_BASINS.items()]
    other = {"source": "esri-active-hurricanes", "ok": True, "as_of": None, "error": None, "ids": [],
             "note": "JTWC warnings relayed by the Esri Living Atlas Active Hurricanes service"}
    try:
        got = cached("esri:tc", ESRI_TTL, _esri_storms, stale_ttl=3 * 3600)
        other["as_of"] = got["as_of"]
        if got.get("stale"):
            other["ok"], other["error"] = False, got.get("error")
        for s in got["storms"]:
            if s["basin"] in nhc.NHC_BASINS:                    # NHC's own basins: NHC is the authority
                continue
            s = dict(s, errors=[], headline=[], next_advisory=None,
                     geometry_source="Esri Active Hurricanes feature service")
            s.pop("files", None)
            storms.append(s)
            other["ids"].append(s["id"])
    except ApiError as e:
        other["ok"], other["error"] = False, describe(e)
    checked.append({"basin": "JTWC", "name": "West Pacific, Indian Ocean, Southern Hemisphere",
                    "source": "esri-active-hurricanes", "ok": other["ok"], "error": other["error"]})
    for s in storms:
        s["nearest_hubs"] = nearest_hubs(s)
    return {
        "source": "noaa-nhc", "freshness": "ADVISORY", "as_of": now,
        "note": "Advisory positions: a system moves here only when its forecasting centre issues a new advisory.",
        "checked": checked, "storms": storms, "other_basins": other,
    }


@router.get("/api/storms")
def storms():
    """Active tropical cyclones worldwide. `storms: []` with `checked` = nothing is active in the basins asked."""
    return cached("nhc:storms", STORMS_TTL, _load_storms, stale_ttl=12 * 3600)


# ------------------------------------------------------------------ reference points
def _hub_public(h: dict) -> dict:
    return {"id": h["id"], "name": h["name"], "kind": h["kind"], "kind_name": HUB_KINDS[h["kind"]],
            "lat": h["lat"], "lon": h["lon"], "what": h["what"]}


@router.get("/api/wx/hubs")
def wx_hubs():
    """Static reference points. `as_of` is the date the coordinates were last checked, not a feed time."""
    return {"source": "reference", "freshness": "REFERENCE", "as_of": None, "checked": "2026-11-12",
            "note": HUB_VERIFIED, "kinds": HUB_KINDS, "hubs": [_hub_public(h) for h in HUBS]}


# ------------------------------------------------------------------ marine observations
def _region_of(lat: float, lon: float) -> str:
    for r in REGIONS:
        w, s, e, n = r["bbox"]
        if w <= lon <= e and s <= lat <= n:
            return r["id"]
    return "other"


def _station_names() -> dict:
    try:
        return cache.fetch("ndbc:stations", STATIONS_TTL, lambda: ndbc.parse_stations(ndbc.fetch_stations()),
                           stale_ttl=14 * 86400, error_ttl=600).value
    except ApiError:
        return {}                                               # ids are still shown; names are a nicety


_READINGS = ("wind_kt", "gust_kt", "wave_m", "pressure_mb", "water_c", "air_c")


def _load_obs() -> dict:
    now = time.time()
    rows = ndbc.parse_latest(ndbc.fetch_latest())
    names = _station_names()
    stations, old, silent = [], 0, 0
    for r in rows:
        if all(r[k] is None for k in _READINGS):
            silent += 1
            continue
        if now - r["t"] > OBS_MAX_AGE:
            old += 1
            continue
        r["name"] = names.get(r["id"].upper())
        r["region"] = _region_of(r["lat"], r["lon"])
        stations.append(r)
    stations.sort(key=lambda r: r["id"])
    hub_obs = {}
    for h in HUBS:
        best = None
        for r in stations:
            if r["wind_kt"] is None and r["wave_m"] is None:
                continue
            if abs(r["lat"] - h["lat"]) > 2.0:                  # cheap reject before the trigonometry
                continue
            km = gc_km(h["lat"], h["lon"], r["lat"], r["lon"])
            if km <= HUB_OBS_KM and (best is None or km < best[0]):
                best = (km, r)
        hub_obs[h["id"]] = {"station": best[1]["id"], "km": round(best[0])} if best else None
    return {
        "source": "ndbc", "freshness": "OBS", "as_of": now,
        "obs_latest": max((r["t"] for r in stations), default=None),
        "note": "Measured by NOAA NDBC buoys and coastal stations; each row carries its own observation time.",
        "total": len(rows), "dropped": {"older_than_6h": old, "no_reading": silent},
        "regions": [{"id": r["id"], "name": r["name"], "bbox": r["bbox"]} for r in REGIONS],
        "hub_obs": hub_obs, "hub_obs_km": HUB_OBS_KM,
        "stations": stations,
    }


@router.get("/api/wx/obs")
def wx_obs(region: str = Query("all", max_length=12, description="all | carib | gulf | east | west | hawaii | other")):
    """Latest measured observation per NDBC station (wind in kt, waves in m, pressure in hPa, temperatures in °C)."""
    if region not in REGION_IDS:
        raise BadRequest("region must be one of " + ", ".join(REGION_IDS))
    out = cached("ndbc:latest", OBS_TTL, _load_obs, stale_ttl=3 * 3600)
    rows = out["stations"] if region == "all" else [r for r in out["stations"] if r["region"] == region]
    return {**out, "region": region, "count": len(rows), "stations": rows}


# ------------------------------------------------------------------ model values where nothing is measured
MODEL_HUBS = [h for h in HUBS if h["kind"] == "chokepoint"]
MODEL_PARTS = (
    ("wind", "open-meteo:hubs-wind", openmeteo.fetch_forecast, openmeteo.FORECAST_FIELDS),
    ("waves", "open-meteo:hubs-waves", openmeteo.fetch_marine, openmeteo.MARINE_FIELDS),
)
_MODEL_NAMES = {"wind_speed_10m": "wind_kt", "wind_direction_10m": "wind_dir", "wind_gusts_10m": "gust_kt",
                "pressure_msl": "pressure_mb", "wave_height": "wave_m", "wave_direction": "wave_dir",
                "wave_period": "wave_period_s", "sea_surface_temperature": "water_c"}


@router.get("/api/wx/model")
def wx_model():
    """Open-Meteo MODEL values at the chokepoints (no NDBC station there). Two upstream calls an hour at most; a
    part that fails is reported in `parts` and its values are null — never replaced by anything."""
    pts = [(h["lat"], h["lon"]) for h in MODEL_HUBS]
    points = [dict(_hub_public(h), t=None, grid_lat=None, grid_lon=None, **{v: None for v in _MODEL_NAMES.values()})
              for h in MODEL_HUBS]
    parts, as_of = [], None
    for what, key, fetch, fields in MODEL_PARTS:
        def load(fetch=fetch, fields=fields):
            return {"as_of": time.time(), "rows": openmeteo.parse_current(fetch(pts), len(pts), fields)}
        try:
            got = cached(key, MODEL_TTL, load, stale_ttl=6 * 3600, error_ttl=MODEL_ERROR_TTL)
        except ApiError as e:
            parts.append({"what": what, "ok": False, "error": describe(e), "as_of": None})
            continue
        parts.append({"what": what, "ok": not got.get("stale"), "error": got.get("error"), "as_of": got["as_of"]})
        as_of = max(as_of or 0, got["as_of"])
        for p, row in zip(points, got["rows"]):
            p["t"] = max(p["t"] or 0, row["t"] or 0) or None
            p["grid_lat"], p["grid_lon"] = row["grid_lat"], row["grid_lon"]
            for f in fields:
                p[_MODEL_NAMES[f]] = row[f]
    return {"source": "open-meteo", "freshness": "MODEL", "as_of": as_of,
            "note": "Forecast-model output at the nearest grid point, not an observation. "
                    "Shown only for reference points that no NDBC station covers.",
            "parts": parts, "points": points}


# ------------------------------------------------------------------ satellite imagery
def _resolve_layer(layer_id: str, now: float) -> dict:
    """The time to request for a layer (providers.gibs.pick_time); for daily true colour the last complete UTC
    day, verified to hold imagery by fetching its zoom-0 tile (which the page needs anyway)."""
    times = gibs.parse_domain(layer_id, gibs.fetch_domain(layer_id, now))
    got = gibs.pick_time(layer_id, times, now)
    if got is None:
        raise UpstreamError("gibs", None, f"GIBS lists no recent imagery for {gibs.LAYERS[layer_id]['gibs']}")
    for cand in got.pop("candidates"):
        try:
            body = TILES.get((layer_id, cand, 0, 0, 0), lambda c=cand: gibs.fetch_tile(layer_id, c, 0, 0, 0))
        except ApiError:
            continue
        if len(body) >= 4000:                                   # an empty (black) mosaic compresses to ~1.7 kB
            if cand != got["time"]:
                got["note"] = f"the mosaic of {got['time']} is empty: showing {cand}"
            got["time"], got["t"] = cand, gibs.time_epoch(layer_id, cand)
            break
    return got


def _load_layers() -> dict:
    now = time.time()

    def one(layer_id):
        try:
            return layer_id, _resolve_layer(layer_id, now), None
        except ApiError as e:
            return layer_id, None, describe(e)
    with ThreadPoolExecutor(max_workers=len(gibs.LAYERS)) as pool:
        results = list(pool.map(one, gibs.LAYERS))
    if all(err for _, _, err in results):
        raise UpstreamError("gibs", None, results[0][2]["message"])
    layers = {}
    for layer_id, got, err in results:
        spec = gibs.LAYERS[layer_id]
        layers[layer_id] = {
            "id": layer_id, "gibs": spec["gibs"], "label": spec["label"], "matrix": spec["matrix"],
            "max_z": spec["max_z"], "ext": spec["ext"], "step": spec["step"], "bbox": spec["bbox"],
            "upstream_template": gibs.upstream_template(layer_id),
            "ok": err is None, "error": err,
            "time": got["time"] if got else None, "t": got["t"] if got else None,
            "newest": got["newest"] if got else None, "note": got["note"] if got else None,
        }
    return {
        "source": "nasa-gibs", "freshness": "IMAGERY", "as_of": now,
        "projection": "EPSG:3857 (web mercator), 256 px tiles",
        "tile_template": "/api/satellite/tile?layer={layer}&time={time}&z={z}&x={x}&y={y}",
        "products": [dict(p, ok=any(layers[i]["ok"] for i in p["parts"])) for p in gibs.PRODUCTS],
        "layers": layers,
    }


@router.get("/api/satellite/layers")
def satellite_layers():
    """Imagery layers that work, each with the time that has imagery right now."""
    return cached("gibs:layers", LAYERS_TTL, _load_layers, stale_ttl=6 * 3600)


@router.get("/api/satellite/tile")
def satellite_tile(layer: str = Query(..., max_length=16), time_: str = Query(..., alias="time", max_length=20),
                   z: int = Query(..., ge=0, le=9), x: int = Query(..., ge=0), y: int = Query(..., ge=0)):
    """One imagery tile, same origin. `layer` must be in the whitelist, `time` must be a time of that layer's own
    form inside the window GIBS keeps, z/x/y inside its matrix set: the upstream URL is built from those only."""
    spec = gibs.LAYERS.get(layer)
    if spec is None:
        raise BadRequest("layer must be one of " + ", ".join(gibs.LAYERS))
    t = gibs.time_epoch(layer, time_)
    if t is None:
        raise BadRequest(f"time must be {'YYYY-MM-DD' if spec['step'] == 'day' else 'YYYY-MM-DDTHH:MM:00Z on the layer step'}")
    now = time.time()
    back = 45 * 86400 if spec["step"] == "day" else 3 * 86400
    if not now - back <= t <= now + 900:
        raise BadRequest("time is outside the period this layer is served for")
    if not gibs.tile_ok(layer, z, x, y):
        raise BadRequest(f"tile {z}/{x}/{y} is outside the layer's matrix set (max zoom {spec['max_z']})")
    try:
        body = TILES.get((layer, time_, z, x, y), lambda: gibs.fetch_tile(layer, time_, z, x, y))
    except gibs.NoTile:
        # GIBS has nothing for this tile (a gap in the layer, or a frame that is not on all of its servers yet):
        # "no content" — the page leaves the tile empty and counts it; it is not a failure of the gateway.
        return Response(status_code=204, headers={"Cache-Control": "no-store", "X-Imagery-Time": time_})
    return Response(content=body, media_type=gibs.MIME[spec["ext"]],
                    headers={"Cache-Control": "private, max-age=21600", "X-Imagery-Time": time_})
