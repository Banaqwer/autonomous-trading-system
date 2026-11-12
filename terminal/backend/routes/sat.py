"""SAT + SHIP slice (S5): /api/sat/* and /api/vessels.

SAT — dated Sentinel-2 L2A scenes of real oil-storage / export sites (Copernicus Data Space)
beside the real EIA weekly crude-stock figure for the site's hub.
SHIP — a server-side vessel store fed by the live AIS hub (position + static data merged),
with counts by region and type and an honest, data-derived coverage statement.

Rules kept here: every upstream call goes through core.cache + core.http; ids/dates/sites are
validated against whitelists / the catalog before any URL is built; Processing Units are spent
at most once per (site, date, bands) — the PNG is cached on disk under backend/.cache/sat/ with
Windows-safe names; vessels are evicted by AGE (never by MMSI order).
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Query
from fastapi.responses import Response

import stream_hub
from core.cache import cached
from core.errors import BadRequest, NoSource, UpstreamError
from core.fanout import HUB
from providers import copernicus, eia_storage

router = APIRouter()

# ----------------------------------------------------------------------- sites
# Oil-storage / export sites whose bounding boxes cover the tank farms themselves (the old
# default sat on the town of Cushing; the tank farms are south of it). bbox = [W, S, E, N]
# in EPSG:4326. `hub` names the EIA weekly crude-stock series that measures the site, or None.
SITES = [
    {"id": "cushing", "name": "Cushing, OK — tank farms",
     "country": "US", "bbox": [-96.785, 35.915, -96.710, 35.980], "hub": "cushing",
     "note": "The NYMEX WTI delivery point; tank farms south of the town of Cushing."},
    {"id": "houston", "name": "Houston Ship Channel — terminals",
     "country": "US", "bbox": [-95.115, 29.700, -95.020, 29.760], "hub": "padd3",
     "note": "Refining and storage terminals along the Houston Ship Channel (PADD 3)."},
    {"id": "rotterdam", "name": "Rotterdam Europoort — tank farms",
     "country": "NL", "bbox": [3.980, 51.915, 4.120, 51.975], "hub": None,
     "note": "Europe's largest oil port; tank terminals in the Europoort / Botlek area."},
    {"id": "fujairah", "name": "Fujairah oil terminal, UAE",
     "country": "AE", "bbox": [56.320, 25.155, 56.375, 25.215], "hub": None,
     "note": "Middle East bunkering and storage hub outside the Strait of Hormuz."},
    {"id": "rastanura", "name": "Ras Tanura, Saudi Arabia",
     "country": "SA", "bbox": [50.080, 26.620, 50.185, 26.715], "hub": None,
     "note": "Saudi Aramco's main crude export terminal and tank farm."},
]
SITE_BY_ID = {s["id"]: s for s in SITES}

SCENE_TTL = 3600            # the catalog search is cheap; one answer per hour per site
SCENE_STALE = 6 * 3600
STORAGE_TTL = 3600          # EIA weekly series changes once a week
STORAGE_STALE = 7 * 86400
CLOUD_MAX = 20.0            # a scene counts as "usable" at or below this cloud percent
SCENE_WINDOW_DAYS = 120     # how far back the catalog search looks
SCENE_DATES_SHOWN = 8       # recent usable dates offered as a date selector

# disk cache for rendered PNGs
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".cache", "sat")
CACHE_MAX_BYTES = 256 * 1024 * 1024
_DISK_LOCK = threading.Lock()
import re as _re

_SAFE = _re.compile(r"[^A-Za-z0-9_.-]")


def _safe_name(*parts) -> str:
    return "_".join(_SAFE.sub("-", str(p)) for p in parts)


def _iso_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


# ----------------------------------------------------------------------- SAT routes
@router.get("/api/sat/sites")
def sat_sites():
    """The storage / export sites the SAT page can show, with their verified bounding boxes
    and the EIA hub that measures each (where one exists)."""
    return {"source": "reference", "as_of": time.time(),
            "sites": [{"id": s["id"], "name": s["name"], "country": s["country"],
                       "bbox": s["bbox"], "hub": s["hub"], "note": s["note"]}
                      for s in SITES]}


def _load_scene(site: dict) -> dict:
    now = time.time()
    frm = _iso_day(now - SCENE_WINDOW_DAYS * 86400)
    to = _iso_day(now + 86400)
    scenes = copernicus.parse_catalog(copernicus.catalog_search(site["bbox"], frm, to))
    usable = [s for s in scenes if s.get("cloud_pct") is not None and s["cloud_pct"] <= CLOUD_MAX]
    # recent usable dates (deduped by day, newest first)
    seen, dates = set(), []
    for s in usable:
        if s["day"] in seen:
            continue
        seen.add(s["day"])
        dates.append({"date": s["day"], "cloud_pct": round(s["cloud_pct"], 1), "t": s["t"]})
        if len(dates) >= SCENE_DATES_SHOWN:
            break
    best = usable[0] if usable else None
    checked_from = scenes[-1]["day"] if scenes else frm
    return {
        "site": site["id"], "source": "copernicus-sentinel2", "as_of": now,
        "satellite": "Sentinel-2 L2A", "resolution_m": copernicus.NATIVE_M_PER_PX,
        "cloud_threshold": CLOUD_MAX, "bbox": site["bbox"],
        "scene": None if not best else {
            "acquired": best["t"], "date": best["day"],
            "cloud_pct": round(best["cloud_pct"], 1), "scene_id": best["id"]},
        "dates": dates,
        "scenes_found": len(scenes), "usable_found": len(usable),
        "window": {"from": frm, "to": to, "checked_from": checked_from},
    }


@router.get("/api/sat/scene")
def sat_scene(site: str = Query(..., max_length=24)):
    """The most recent Sentinel-2 L2A acquisition under the cloud threshold for a site, plus
    the list of recent usable dates. Catalog only — no processing units are spent."""
    s = SITE_BY_ID.get(site)
    if s is None:
        raise BadRequest("unknown site — one of " + ", ".join(SITE_BY_ID))
    out = cached(f"copernicus:scene:{site}", SCENE_TTL, lambda: _load_scene(s),
                 stale_ttl=SCENE_STALE)
    return {**out, "image_url": (f"/api/sat/image?site={site}&date={out['scene']['date']}"
                                 if out.get("scene") else None)}


def _usable_days(site: str) -> set:
    try:
        sc = cached(f"copernicus:scene:{site}", SCENE_TTL, lambda: _load_scene(SITE_BY_ID[site]),
                    stale_ttl=SCENE_STALE)
    except Exception:
        return set()
    return {d["date"] for d in sc.get("dates", [])}


def _disk_path(site: str, date: str, bands: str) -> str:
    return os.path.join(CACHE_DIR, _safe_name(site, date, bands) + ".png")


def _prune_cache():
    try:
        files = []
        total = 0
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= CACHE_MAX_BYTES:
            return
        files.sort()  # oldest first
        for _, size, p in files:
            if total <= CACHE_MAX_BYTES:
                break
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass
    except OSError:
        pass


@router.get("/api/sat/image")
def sat_image(site: str = Query(..., max_length=24),
              date: str = Query(..., max_length=10),
              bands: str = Query("truecolor", max_length=12)):
    """PNG bytes of one dated Sentinel-2 scene rendered for the site's bbox at the true aspect
    ratio. Cached on disk; a (site, date, bands) is rendered at most once. site and date are
    validated against the whitelist and the catalog before any processing request is made."""
    s = SITE_BY_ID.get(site)
    if s is None:
        raise BadRequest("unknown site — one of " + ", ".join(SITE_BY_ID))
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""):
        raise BadRequest("date must be YYYY-MM-DD")
    if bands not in copernicus.EVALSCRIPTS:
        raise BadRequest("bands must be one of " + ", ".join(copernicus.EVALSCRIPTS))
    path = _disk_path(site, date, bands)
    hdrs = {"Cache-Control": "private, max-age=86400",
            "X-Scene-Date": date, "X-Scene-Site": site}
    # serve the cached file if we already rendered this scene
    with _DISK_LOCK:
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    body = f.read()
                os.utime(path, None)
                return Response(content=body, media_type="image/png", headers=hdrs)
            except OSError:
                pass
    # not cached: the date must be a usable acquisition the catalog reported (guards PU spend)
    if date not in _usable_days(site):
        raise NoSource(f"{date} is not a recent usable Sentinel-2 acquisition for {site}; "
                       f"ask /api/sat/scene?site={site} for the dates that are")
    # single-flight on the render so two viewers do not spend two processing units
    out = cached(f"copernicus:render:{site}:{date}:{bands}", 3600,
                 lambda: {"png": copernicus.render(s["bbox"], date, bands), "as_of": time.time()},
                 stale_ttl=24 * 3600)
    body = out["png"]
    with _DISK_LOCK:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
            _prune_cache()
        except OSError:
            pass
    return Response(content=body, media_type="image/png", headers=hdrs)


@router.get("/api/sat/storage")
def sat_storage(hub: str = Query(..., max_length=12), length: int = Query(52)):
    """The real EIA weekly crude-oil ending stocks for a hub (Cushing / PADD 3 / US ex-SPR),
    with the week-ending date, change vs the prior week and the history for a chart."""
    if hub not in eia_storage.SERIES:
        raise BadRequest("hub must be one of " + ", ".join(eia_storage.SERIES))
    length = max(2, min(int(length), 520))

    def load():
        d = eia_storage.storage(hub, length)
        d["source"] = "eia"
        d["freshness"] = "WEEKLY"
        d["as_of"] = time.time()
        return d

    return cached(f"eia:storage:{hub}:{length}", STORAGE_TTL, load, stale_ttl=STORAGE_STALE)


# ----------------------------------------------------------------------- vessels
VESSEL_MAX = 8000           # store cap (then the oldest are dropped)
VESSEL_TTL = 1800.0         # a vessel is dropped if it has not reported for this long
# regions for the coverage statement: [W, S, E, N]
REGIONS = [
    ("europe_nw", "NW Europe (North Sea / Baltic / Channel)", [-12, 48, 32, 66]),
    ("europe_med", "Mediterranean & Black Sea", [-6, 30, 42, 48]),
    ("na_east", "North America — East & Gulf", [-100, 18, -60, 50]),
    ("na_west", "North America — West", [-170, 30, -110, 62]),
    ("samerica", "South America", [-82, -56, -34, 14]),
    ("mideast", "Middle East (Gulf / Red Sea / Arabian Sea)", [32, 10, 65, 32]),
    ("sasia", "South Asia", [65, 5, 92, 28]),
    ("seasia", "Southeast Asia", [92, -11, 142, 23]),
    ("easia", "East Asia (China / Japan / Korea)", [108, 20, 150, 46]),
    ("africa", "Africa", [-18, -36, 52, 18]),
    ("oceania", "Australia & Oceania", [110, -48, 180, -5]),
]


class _Store:
    """Thread-safe vessel store. Hooks write (event-loop thread); routes read (threadpool)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pos: dict = {}          # mmsi -> position record
        self.meta: dict = {}         # mmsi -> static record
        self.first_seen = None
        self.updates = 0

    def on_vessel(self, ev):
        mmsi = ev.get("mmsi")
        if mmsi is None or ev.get("lat") is None or ev.get("lon") is None:
            return
        now = time.time()
        with self.lock:
            if self.first_seen is None:
                self.first_seen = now
            self.updates += 1
            self.pos[mmsi] = {"mmsi": mmsi, "name": (ev.get("name") or "").strip() or None,
                              "lat": ev.get("lat"), "lon": ev.get("lon"),
                              "sog": ev.get("sog"), "cog": ev.get("cog"),
                              "heading": ev.get("heading"), "t": now}
            if len(self.pos) > VESSEL_MAX:
                # drop the oldest by last report time (never by MMSI order)
                oldest = sorted(self.pos.items(), key=lambda kv: kv[1]["t"])
                for mmsi2, _ in oldest[:len(self.pos) - VESSEL_MAX]:
                    self.pos.pop(mmsi2, None)

    def on_static(self, ev):
        mmsi = ev.get("mmsi")
        if mmsi is None:
            return
        with self.lock:
            self.meta[mmsi] = {k: v for k, v in ev.items() if k not in ("type", "src")}
            if len(self.meta) > VESSEL_MAX * 2:
                for mmsi2 in list(self.meta)[:len(self.meta) - VESSEL_MAX * 2]:
                    self.meta.pop(mmsi2, None)

    def snapshot(self, max_age: float) -> dict:
        from providers.ais import type_group
        now = time.time()
        with self.lock:
            # expire old positions
            dead = [m for m, v in self.pos.items() if now - v["t"] > VESSEL_TTL]
            for m in dead:
                self.pos.pop(m, None)
            rows = []
            for m, v in self.pos.items():
                if now - v["t"] > max_age:
                    continue
                meta = self.meta.get(m) or {}
                stype = meta.get("ship_type")
                rows.append({
                    "mmsi": m, "name": v.get("name") or meta.get("name"),
                    "lat": v["lat"], "lon": v["lon"], "sog": v.get("sog"),
                    "cog": v.get("cog"), "heading": v.get("heading"),
                    "ship_type": stype,
                    "ship_type_group": meta.get("ship_type_group") or type_group(stype),
                    "destination": meta.get("destination"),
                    "length": meta.get("length"), "width": meta.get("width"),
                    "draught": meta.get("draught"), "t": v["t"]})
            first_seen = self.first_seen
            updates = self.updates
        return {"rows": rows, "first_seen": first_seen, "updates": updates}


STORE = _Store()
HUB.on_hub("vessel", STORE.on_vessel)
HUB.on_hub("vessel_static", STORE.on_static)


def _ais_feed_state() -> dict:
    try:
        feeds = stream_hub.snapshot().get("feeds", {})
    except Exception:
        feeds = {}
    f = feeds.get("ais")
    if not f:
        return {"enabled": False, "state": "disabled",
                "reason": "the AIS feed is not enabled on this gateway (OT_FEEDS has no 'ais')"}
    return {"enabled": f.get("state") not in ("disabled", None),
            "state": f.get("state"),
            "reason": f.get("last_error") or None}


def _region_of(lon, lat):
    for rid, _name, (w, s, e, n) in REGIONS:
        if w <= lon <= e and s <= lat <= n:
            return rid
    return "other"


@router.get("/api/vessels")
def vessels(max_age: int = Query(900), limit: int = Query(6000)):
    """Live AIS vessels from the server-side store: positions merged with static data (type,
    name, destination, dimensions), counts by region and by type, and an honest coverage
    statement computed from the data. Evicted by age, never by MMSI."""
    max_age = max(30, min(int(max_age), int(VESSEL_TTL)))
    limit = max(1, min(int(limit), VESSEL_MAX))
    feed = _ais_feed_state()
    snap = STORE.snapshot(max_age)
    rows = snap["rows"]

    by_region = {}
    region_names = {rid: name for rid, name, _ in REGIONS}
    region_names["other"] = "Other / open ocean"
    by_type = {}
    typed = 0
    for v in rows:
        r = _region_of(v["lon"], v["lat"])
        by_region[r] = by_region.get(r, 0) + 1
        g = v["ship_type_group"] or "unknown"
        by_type[g] = by_type.get(g, 0) + 1
        if v["ship_type"] is not None:
            typed += 1

    reporting = sorted(((region_names.get(r, r), n) for r, n in by_region.items()),
                       key=lambda kv: kv[1], reverse=True)
    if feed["enabled"] and rows:
        top = reporting[0][0] if reporting else "—"
        coverage = ("AISStream is terrestrial and volunteer-fed, so coverage is uneven. "
                    f"Reporting now: " + ", ".join(f"{n} in {name}" for name, n in reporting[:6])
                    + f". Most vessels are in {top}. "
                    f"{typed} of {len(rows)} have broadcast a ship type.")
    elif feed["enabled"]:
        coverage = ("AIS feed is up but no vessels have reported yet — "
                    "the volunteer network can be quiet for a region at any moment.")
    else:
        coverage = feed["reason"] or "the AIS feed is not available on this gateway"

    rows.sort(key=lambda v: v["t"], reverse=True)
    return {
        "source": "aisstream", "freshness": "RT", "as_of": time.time(),
        "feed": feed, "count": len(rows), "max_age": max_age,
        "typed_count": typed,
        "counts_by_region": [{"id": r, "name": region_names.get(r, r), "n": n}
                             for r, n in sorted(by_region.items(), key=lambda kv: kv[1], reverse=True)],
        "counts_by_type": [{"group": g, "n": n}
                           for g, n in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)],
        "coverage": coverage,
        "store": {"first_seen": snap["first_seen"], "updates": snap["updates"],
                  "held": len(STORE.pos), "static_known": len(STORE.meta)},
        "vessels": rows[:limit],
    }
