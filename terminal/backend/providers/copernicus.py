"""Copernicus Data Space (CDSE) — dated Sentinel-2 L2A imagery for the SAT module (owner S5).

Three pieces, all through core.http (status board, scrubbed errors, proxy, timeouts):
  - token():            OAuth2 client-credentials bearer, cached ~9 min in-process.
  - catalog_search():   POST the STAC catalog -> the Sentinel-2 L2A scenes over a bbox in a
                        date window, with acquisition time and eo:cloud_cover. CHEAP (no
                        processing units). Pure parser: parse_catalog().
  - render():           POST the Process API for ONE scene limited to one calendar day and
                        return the PNG bytes. COSTS PROCESSING UNITS — the route renders a
                        site+date at most once and caches the bytes on disk; never on a poll.

Needs CDSE_CLIENT_ID + CDSE_CLIENT_SECRET (in config.py; values never printed/logged).
Recipe: terminal/audit/geo-recipes.json recipe "Real, dated Sentinel-2 image …".
stdlib + core only (Python 3.9).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse

from core import http, status
from core.errors import NotConfigured, UpstreamError

SOURCE = "copernicus"
TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
             "protocol/openid-connect/token")
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
COLLECTION = "sentinel-2-l2a"

# Sentinel-2 ground sampling distance of the visible bands.
NATIVE_M_PER_PX = 10.0
# Hard ceiling on a rendered image so the Process request and the cached file stay bounded.
MAX_DIM = 1536

# True-colour (B04/B03/B02) with a gentle tone curve; UINT8 output so the PNG is small.
_EVAL_TRUECOLOR = """//VERSION=3
function setup() {
  return { input: ["B04", "B03", "B02"], output: { bands: 3, sampleType: "UINT8" } };
}
function s(v) { return 255 * Math.min(1, Math.pow(3.5 * v, 0.8)); }
function evaluatePixel(p) { return [s(p.B04), s(p.B03), s(p.B02)]; }
"""

# False-colour (B08/B04/B03): vegetation red, water dark, tanks/roofs grey — reads storage well.
_EVAL_FALSECOLOR = """//VERSION=3
function setup() {
  return { input: ["B08", "B04", "B03"], output: { bands: 3, sampleType: "UINT8" } };
}
function s(v) { return 255 * Math.min(1, Math.pow(2.8 * v, 0.8)); }
function evaluatePixel(p) { return [s(p.B08), s(p.B04), s(p.B03)]; }
"""

EVALSCRIPTS = {"truecolor": _EVAL_TRUECOLOR, "falsecolor": _EVAL_FALSECOLOR}

_TOKEN = {"value": None, "exp": 0.0}
_TOKEN_LOCK = threading.Lock()


def configured() -> bool:
    return bool(os.environ.get("CDSE_CLIENT_ID") and os.environ.get("CDSE_CLIENT_SECRET"))


def token(timeout: float = 20) -> str:
    """A bearer token, cached until ~60 s before it expires. Raises NotConfigured /
    UpstreamError. The client secret is form-encoded in the body, never in a URL."""
    now = time.time()
    with _TOKEN_LOCK:
        if _TOKEN["value"] and now < _TOKEN["exp"]:
            return _TOKEN["value"]
        cid = os.environ.get("CDSE_CLIENT_ID")
        csec = os.environ.get("CDSE_CLIENT_SECRET")
        if not cid or not csec:
            raise NotConfigured("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set "
                                "(free at dataspace.copernicus.eu)", source=SOURCE)
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": cid, "client_secret": csec}).encode()
        code, _, raw = http.request(
            TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json"},
            timeout, source=SOURCE, data=body, method="POST")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise UpstreamError(SOURCE, code, "token reply is not JSON") from None
        tok = payload.get("access_token")
        if not tok:
            raise UpstreamError(SOURCE, code, "token reply had no access_token")
        ttl = float(payload.get("expires_in") or 600)
        _TOKEN["value"] = tok
        _TOKEN["exp"] = now + max(60.0, ttl - 60.0)
        return tok


# ------------------------------------------------------------------ catalog (cheap)
def catalog_search(bbox, date_from: str, date_to: str, limit: int = 50,
                   timeout: float = 25) -> dict:
    """Raw STAC search payload: Sentinel-2 L2A scenes over `bbox` in [date_from, date_to].
    `date_*` are YYYY-MM-DD (UTC). No processing units are spent."""
    payload = {
        "bbox": [float(x) for x in bbox],
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "collections": [COLLECTION],
        "limit": max(1, min(int(limit), 100)),
        "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover"],
                   "exclude": ["geometry", "links", "assets", "bbox"]},
    }
    code, _, raw = http.request(
        CATALOG_URL, {"Authorization": "Bearer " + token(timeout),
                      "Content-Type": "application/json",
                      "Accept": "application/geo+json"},     # CDSE STAC rejects plain application/json (406)
        timeout, source=SOURCE, data=json.dumps(payload).encode(), method="POST")
    try:
        return json.loads(raw)
    except ValueError:
        raise UpstreamError(SOURCE, code, "catalog reply is not JSON") from None


def parse_catalog(payload: dict) -> list:
    """[{id, datetime, t (epoch s), day (YYYY-MM-DD), cloud_pct}] newest first.

    Scenes without a usable datetime are dropped. cloud_pct may be None when the scene
    did not carry eo:cloud_cover."""
    from core.util import iso_epoch, num
    feats = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(feats, list):
        raise UpstreamError(SOURCE, None, "catalog reply has no features array")
    out = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        dtiso = props.get("datetime")
        t = iso_epoch(dtiso)
        if t is None:
            continue
        cloud = num(props.get("eo:cloud_cover"))
        out.append({"id": f.get("id"), "datetime": dtiso, "t": t,
                    "day": str(dtiso)[:10], "cloud_pct": cloud})
    out.sort(key=lambda r: r["t"], reverse=True)
    return out


def pick_scene(scenes: list, max_cloud: float) -> dict | None:
    """The most recent scene at or below `max_cloud` percent cloud (a scene with no cloud
    figure is treated as unknown and skipped)."""
    for s in scenes:
        c = s.get("cloud_pct")
        if c is not None and c <= max_cloud:
            return s
    return None


# ------------------------------------------------------------------ render (costs PU)
def dims_for_bbox(bbox, m_per_px: float = NATIVE_M_PER_PX) -> tuple:
    """Pixel (width, height) for a lon/lat bbox at a given ground resolution, keeping the
    true aspect ratio and clamped to MAX_DIM. Uses a local equirectangular metric."""
    import math
    w, s, e, n = (float(x) for x in bbox)
    midlat = (s + n) / 2.0
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * max(0.05, math.cos(math.radians(midlat)))
    width_m = abs(e - w) * m_per_deg_lon
    height_m = abs(n - s) * m_per_deg_lat
    px_w = max(16, round(width_m / m_per_px))
    px_h = max(16, round(height_m / m_per_px))
    scale = min(1.0, MAX_DIM / max(px_w, px_h))
    return (max(16, round(px_w * scale)), max(16, round(px_h * scale)))


def render(bbox, day: str, bands: str = "truecolor", width: int = None,
           height: int = None, timeout: float = 60) -> bytes:
    """PNG bytes of one Sentinel-2 L2A scene over `bbox`, limited to the single calendar
    day `day` (YYYY-MM-DD). COSTS PROCESSING UNITS — callers cache the result on disk and
    never call this on a page poll. Raises NotConfigured / UpstreamError."""
    if bands not in EVALSCRIPTS:
        bands = "truecolor"
    if width is None or height is None:
        width, height = dims_for_bbox(bbox)
    width = max(16, min(int(width), MAX_DIM))
    height = max(16, min(int(height), MAX_DIM))
    payload = {
        "input": {
            "bounds": {"bbox": [float(x) for x in bbox],
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": COLLECTION,
                      "dataFilter": {"timeRange": {"from": f"{day}T00:00:00Z",
                                                   "to": f"{day}T23:59:59Z"},
                                     "mosaickingOrder": "leastCC",
                                     "maxCloudCoverage": 100}}],
        },
        "output": {"width": width, "height": height,
                   "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": EVALSCRIPTS[bands],
    }
    code, _, raw = http.request(
        PROCESS_URL, {"Authorization": "Bearer " + token(timeout),
                      "Content-Type": "application/json", "Accept": "image/png"},
        timeout, source=SOURCE, data=json.dumps(payload).encode(), method="POST")
    if not raw:
        raise UpstreamError(SOURCE, code, "process API returned an empty image")
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        # CDSE sends a JSON error body with a 200 in some failure modes.
        snippet = raw[:160].decode("utf-8", "replace")
        raise UpstreamError(SOURCE, code, f"process API did not return PNG: {snippet}")
    status.ok(SOURCE)
    return raw
