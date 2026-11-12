"""NASA GIBS — satellite imagery tiles (WMTS REST, web-mercator "GoogleMapsCompatible" sets). Keyless.

    {BASE}/{Layer}/default/{Time}/{TileMatrixSet}/{z}/{y}/{x}.{ext}        256 px XYZ tiles, y = row, x = column

`LAYERS` is the whitelist: identifier, matrix set (= deepest zoom), extension and time step of each layer
were read from GIBS' WMTSCapabilities and every template was fetched as a real tile (2026-11-12).
The old table used one matrix set and `.jpg` for everything, which is HTTP 400 for half of them.

Time: which times a layer really has comes from WMTS DescribeDomains with a time filter (a 200–300 byte
answer; the unfiltered one is up to a megabyte for the 10-minute layers). Three things seen on the real
service on 2026-11-12 shape `pick_time`:
  * the 10-minute layers have gaps (GeoColor West had 16:10, 16:30, 16:50 but not 16:20 / 16:40), and a time
    that is not in the domain is HTTP 404 for every tile — so only listed times are ever requested;
  * the newest frame is still spreading over GIBS' servers: the same tile answered 200 and 404 in turn for
    some minutes, and the literal time `default` resolved to 16:30 on one request and 16:50 on the next — so
    the page is given the newest frame that is at least 20 minutes older than the newest listed one, a tile
    answered 404 is asked for once more, and one that stays missing is "no tile" (not an upstream failure);
  * for the daily true-colour mosaic the newest date is TODAY, which is filled swath by swath (black over
    the Americas in the UTC morning) — the last complete day is used.

`TileStore` is the bounded cache of the tile proxy: least-recently-used, capped in bytes and entries,
one upstream request per tile however many clients ask (GIBS sends `no-store`, so without it every
redraw of the map would be a new download).
"""
from __future__ import annotations

import calendar
import re
import threading
import time
from collections import OrderedDict

from core import http, status
from core.errors import UpstreamError

SOURCE = "gibs"
BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
STEP_S = {"day": 86400, "10min": 600, "30min": 1800}
SETTLE_S = {"day": 0, "10min": 1200, "30min": 0}     # how far behind the newest listed frame the page stays
DOMAIN_WINDOW_S = {"day": 10 * 86400, "10min": 12 * 3600, "30min": 36 * 3600}

# bbox = [west, south, east, north] of the part of the layer the page draws (east may exceed 180). A geostationary
# satellite sees one disc of the globe; where two discs overlap, each is used on its own side of the meridian
# half-way between the two satellites (GOES-West 137.2°W | 106°W | GOES-East 75.2°W; Himawari 140.7°E | 178°W |
# GOES-West), where both look at the ground equally obliquely. The page asks for no tile outside the box and
# clips the layer to it, so nothing is drawn twice.
LAYERS = {
    "geo_west": {"gibs": "GOES-West_ABI_GeoColor", "matrix": "GoogleMapsCompatible_Level7", "max_z": 7, "ext": "png",
                 "step": "10min", "label": "GOES-West", "bbox": [-217.0, -81.0, -106.0, 81.0]},
    "geo_east": {"gibs": "GOES-East_ABI_GeoColor", "matrix": "GoogleMapsCompatible_Level7", "max_z": 7, "ext": "png",
                 "step": "10min", "label": "GOES-East", "bbox": [-106.0, -81.0, 6.0, 81.0]},
    "ir_west": {"gibs": "GOES-West_ABI_Band13_Clean_Infrared", "matrix": "GoogleMapsCompatible_Level6", "max_z": 6,
                "ext": "png", "step": "10min", "label": "GOES-West", "bbox": [-178.0, -81.0, -106.0, 81.0]},
    "ir_east": {"gibs": "GOES-East_ABI_Band13_Clean_Infrared", "matrix": "GoogleMapsCompatible_Level6", "max_z": 6,
                "ext": "png", "step": "10min", "label": "GOES-East", "bbox": [-106.0, -81.0, 6.0, 81.0]},
    "ir_himawari": {"gibs": "Himawari_AHI_Band13_Clean_Infrared", "matrix": "GoogleMapsCompatible_Level6", "max_z": 6,
                    "ext": "png", "step": "10min", "label": "Himawari", "bbox": [60.0, -81.0, 182.0, 81.0]},
    "viirs": {"gibs": "VIIRS_NOAA20_CorrectedReflectance_TrueColor", "matrix": "GoogleMapsCompatible_Level9", "max_z": 9,
              "ext": "jpg", "step": "day", "label": "VIIRS NOAA-20", "bbox": [-180.0, -85.0, 180.0, 85.0]},
    "precip": {"gibs": "IMERG_Precipitation_Rate_30min", "matrix": "GoogleMapsCompatible_Level6", "max_z": 6,
               "ext": "png", "step": "30min", "label": "IMERG", "bbox": [-180.0, -60.0, 180.0, 60.0]},
    "sst": {"gibs": "GHRSST_L4_MUR_Sea_Surface_Temperature", "matrix": "GoogleMapsCompatible_Level7", "max_z": 7,
            "ext": "png", "step": "day", "label": "GHRSST MUR", "bbox": [-180.0, -85.0, 180.0, 85.0]},
}
MIME = {"png": "image/png", "jpg": "image/jpeg"}

# What the page offers: one product = one or more layers drawn together.
PRODUCTS = [
    {"id": "geocolor", "label": "GeoColor clouds", "parts": ["geo_west", "geo_east"], "cadence": "10 min",
     "what": "True colour by day, infrared by night, from the two GOES geostationary satellites: the Americas, "
             "the Atlantic and the East / Central Pacific (the basins NHC forecasts for)."},
    {"id": "ir", "label": "Infrared cloud tops", "parts": ["ir_west", "ir_east", "ir_himawari"], "cadence": "10 min",
     "what": "Clean infrared window (10.3 µm), colour-enhanced: cold = high cloud tops = deep convection. "
             "GOES-West, GOES-East and Himawari; no geostationary coverage of Africa / Europe / Indian Ocean in GIBS."},
    {"id": "viirs", "label": "True colour (daily)", "parts": ["viirs"], "cadence": "daily",
     "what": "VIIRS on NOAA-20, corrected reflectance: one daytime pass per place per day, global. "
             "Shown for the last complete UTC day (today's mosaic is still being filled)."},
    {"id": "precip", "label": "Rain rate", "parts": ["precip"], "cadence": "30 min",
     "what": "IMERG multi-satellite precipitation rate, 60°S–60°N, published several hours behind real time."},
    {"id": "sst", "label": "Sea-surface temperature", "parts": ["sst"], "cadence": "daily",
     "what": "GHRSST MUR analysed sea-surface temperature, one to two days behind."},
]

_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):00Z$")


def time_epoch(layer_id: str, value) -> float | None:
    """A time string in the form this layer takes ('2026-11-11' / '2026-11-12T16:30:00Z') -> epoch s; None if
    it is not exactly that form, not a real date, or off the layer's time step."""
    spec = LAYERS.get(layer_id)
    if spec is None or not isinstance(value, str):
        return None
    m = _DAY.match(value) if spec["step"] == "day" else _STAMP.match(value)
    if not m:
        return None
    g = [int(x) for x in m.groups()] + [0] * 2
    try:
        t = calendar.timegm((g[0], g[1], g[2], g[3], g[4], 0))
    except (ValueError, OverflowError):
        return None
    if time.strftime("%Y-%m-%d", time.gmtime(t)) != value[:10]:      # 2026-02-31 and friends
        return None
    return float(t) if t % STEP_S[spec["step"]] == 0 else None


def fmt_time(layer_id: str, t: float) -> str:
    return time.strftime("%Y-%m-%d" if LAYERS[layer_id]["step"] == "day" else "%Y-%m-%dT%H:%M:00Z", time.gmtime(t))


def tile_ok(layer_id: str, z: int, x: int, y: int) -> bool:
    spec = LAYERS.get(layer_id)
    return spec is not None and 0 <= z <= spec["max_z"] and 0 <= x < (1 << z) and 0 <= y < (1 << z)


def tile_url(layer_id: str, when: str, z: int, x: int, y: int) -> str:
    """Upstream URL of one tile. Everything in it is from the whitelist or an integer; `when` is a string that
    passed time_epoch() (never the literal `default`: it resolves differently from one GIBS server to the next)."""
    spec = LAYERS[layer_id]
    if not tile_ok(layer_id, z, x, y) or time_epoch(layer_id, when) is None:
        raise ValueError("tile outside the layer's matrix set or time form")
    return f"{BASE}/{spec['gibs']}/default/{when}/{spec['matrix']}/{int(z)}/{int(y)}/{int(x)}.{spec['ext']}"


def upstream_template(layer_id: str) -> str:
    spec = LAYERS[layer_id]
    return f"{BASE}/{spec['gibs']}/default/{{time}}/{spec['matrix']}/{{z}}/{{y}}/{{x}}.{spec['ext']}"


class NoTile(UpstreamError):
    """GIBS has no tile for this layer / time / position (HTTP 404, twice)."""


def fetch_tile(layer_id: str, when: str, z: int, x: int, y: int, timeout: float = 12, pause: float = 0.3) -> bytes:
    """One tile's image bytes. A request that gets no HTTP answer, or a 404 (the newest frames are not on every
    GIBS server yet), is made once more; a second 404 is NoTile."""
    url = tile_url(layer_id, when, z, x, y)
    for attempt in (1, 2):
        try:
            _, headers, body = http.request(url, {"Accept": "image/*"}, timeout, source=SOURCE)
            break
        except UpstreamError as e:
            if attempt == 2 and e.status == 404:
                raise NoTile(SOURCE, 404, f"GIBS has no {LAYERS[layer_id]['gibs']} tile {z}/{y}/{x} for {when}") from None
            if attempt == 2 or e.status not in (None, 404):
                raise
            time.sleep(pause)
    ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    if not str(ctype).lower().startswith("image/") or not body:
        raise UpstreamError(SOURCE, None, "GIBS did not return an image for this tile")
    return body


def domain_url(layer_id: str, now: float) -> str:
    """DescribeDomains for the recent past of one layer (identifier and matrix set from the whitelist)."""
    spec = LAYERS[layer_id]
    step = STEP_S[spec["step"]]
    start = (int(now - DOMAIN_WINDOW_S[spec["step"]]) // step) * step
    end = (int(now) // step + 1) * step + (86400 if spec["step"] == "day" else 3600)
    # GIBS does not decode %3A / %2F in the time range: it is appended as it is (digits, "-", "T", ":", "Z", "/"
    # only — both ends are produced by fmt_time).
    return (BASE + "/wmts.cgi?" + http.qs({
        "SERVICE": "WMTS", "REQUEST": "DescribeDomains", "VERSION": "1.0.0", "Domains": "time",
        "LAYER": spec["gibs"], "TileMatrixSet": spec["matrix"]})
        + "&time=" + fmt_time(layer_id, start) + "/" + fmt_time(layer_id, end))


def fetch_domain(layer_id: str, now: float, timeout: float = 12) -> str:
    return http.get_text(domain_url(layer_id, now), timeout=timeout, source=SOURCE)


def parse_domain(layer_id: str, xml_text: str) -> list[float]:
    """DescribeDomains answer -> every time the layer has in the asked window, ascending (epoch s)."""
    m = re.search(r"<Domain>(.*?)</Domain>", xml_text or "", re.S)
    if m is None:
        raise UpstreamError(SOURCE, None, "GIBS DescribeDomains answered without a time domain")
    step, out = STEP_S[LAYERS[layer_id]["step"]], set()
    for period in m.group(1).split(","):
        p = period.strip().split("/")
        if len(p) < 2:
            continue
        a, b = time_epoch(layer_id, p[0]), time_epoch(layer_id, p[1])
        if a is None or b is None or b < a:
            continue
        t = a
        while t <= b and len(out) < 5000:
            out.add(t)
            t += step
    return sorted(out)


def pick_time(layer_id: str, times: list, now: float) -> dict:
    """Which of the available times the page should request -> {time, t, newest, note, older}; None if there is none.
    `older` = up to three listed times before it, newest first (a single tile of a frame can be missing on GIBS).
    Daily true colour: the candidates before today, newest first, are returned as `candidates` for the caller
    to verify (it has the tile cache)."""
    if not times:
        return None
    spec = LAYERS[layer_id]
    newest, pick, note, candidates = times[-1], times[-1], None, []
    if spec["step"] != "day":
        settled = [t for t in times if t <= newest - SETTLE_S[spec["step"]]]
        if settled and settled[-1] != newest:
            pick = settled[-1]
            note = ("the newest frame (" + fmt_time(layer_id, newest)[11:16] + "Z) is still being distributed by GIBS: "
                    "showing the newest one that is complete")
    elif layer_id == "viirs":
        today = int(now // 86400) * 86400
        candidates = [t for t in reversed(times) if t < today][:4]
        if candidates and newest >= today:
            pick = candidates[0]
            note = "today's mosaic (" + fmt_time(layer_id, newest) + ") is still being filled: showing the last complete day"
    older = [t for t in times if t < pick][-3:][::-1]          # listed frames just before: the page's fallback for a tile GIBS lacks
    return {"time": fmt_time(layer_id, pick), "t": pick, "newest": fmt_time(layer_id, newest), "note": note,
            "older": [fmt_time(layer_id, t) for t in older],
            "candidates": [fmt_time(layer_id, t) for t in candidates]}


class _Flight:
    __slots__ = ("done", "value", "exc")

    def __init__(self):
        self.done, self.value, self.exc = threading.Event(), None, None


class TileStore:
    """Bounded LRU of tile bytes with single-flight loading and a short memory of failures."""

    def __init__(self, max_bytes: int = 96 * 1024 * 1024, max_items: int = 3000, fail_ttl: float = 15.0):
        self.max_bytes, self.max_items, self.fail_ttl = max_bytes, max_items, fail_ttl
        self._lock = threading.Lock()
        self._items: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._flights: dict = {}
        self._fails: dict = {}
        self.bytes = 0
        self.hits = self.loads = self.evictions = 0

    def get(self, key: tuple, loader) -> bytes:
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                self.hits += 1
                status.hit(SOURCE)
                return hit
            failed = self._fails.get(key)
            if failed is not None:
                if time.time() < failed[1]:
                    raise failed[0]
                del self._fails[key]
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            flight.done.wait(30.0)
            if flight.exc is not None:
                raise flight.exc
            if flight.value is None:
                raise UpstreamError(SOURCE, None, "still waiting for the tile")
            status.hit(SOURCE)
            return flight.value
        try:
            value = loader()
            if not isinstance(value, (bytes, bytearray)) or not value:
                raise UpstreamError(SOURCE, None, "empty tile")
            value = bytes(value)
        except Exception as exc:
            flight.exc = exc
            with self._lock:
                self._flights.pop(key, None)
                self._fails[key] = (exc, time.time() + self.fail_ttl)
                if len(self._fails) > 2000:
                    self._fails.clear()
            flight.done.set()
            raise
        with self._lock:
            self._flights.pop(key, None)
            self.loads += 1
            if len(value) <= self.max_bytes:
                self._items[key] = value
                self.bytes += len(value)
                while self._items and (self.bytes > self.max_bytes or len(self._items) > self.max_items):
                    _, old = self._items.popitem(last=False)
                    self.bytes -= len(old)
                    self.evictions += 1
        flight.value = value
        flight.done.set()
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._fails.clear()
            self.bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {"tiles": len(self._items), "bytes": self.bytes, "max_bytes": self.max_bytes,
                    "max_tiles": self.max_items, "hits": self.hits, "loads": self.loads, "evictions": self.evictions}
