"""AISStream.io — live global AIS vessel tracking for the SHIP module (owner S5).
Free key: AISSTREAM_API_KEY (aisstream.io). WebSocket wss://stream.aisstream.io/v0/stream.
The subscribe message MUST be sent within 3 s of connecting. The key stays server-side.

`AIS_WS`, `subscribe_message()`, `parse_position()` and `parse_static()` are the protocol
pieces; stream_hub.py drives them inside its supervised (auto-reconnecting) session:
  - parse_position(msg)  -> position dict (or None) for a PositionReport      -> "vessel" event
  - parse_static(msg)    -> static dict (or None) for a ShipStaticData msg     -> "vessel_static" event

AISStream is terrestrial and volunteer-fed: coverage is uneven (strong in Europe, patchy
elsewhere). The honest coverage statement on the page is computed from the data that arrives,
not claimed here. Bounding box: the whole globe (one box) — the store reports which regions
are actually sending, so a wider subscription gives a truer coverage picture than a few
hand-picked boxes would, and the hub already throttles the fan-out to one batch per second.
"""
from __future__ import annotations
import os
import json

AIS_WS = "wss://stream.aisstream.io/v0/stream"

# Whole globe as one bounding box: [[[lat, lon] SW], [[lat, lon] NE]] per AISStream's format.
WORLD_BBOX = [[[-90, -180], [90, 180]]]

# ITU ship-type ranges (first digit of the AIS Type code).
SHIP_TYPE_GROUPS = [
    (30, 30, "fishing"), (31, 32, "towing"), (33, 35, "special"), (36, 37, "pleasure"),
    (40, 49, "high-speed"), (50, 59, "special"), (60, 69, "passenger"),
    (70, 79, "cargo"), (80, 89, "tanker"), (90, 99, "other"),
]


def type_group(code) -> str:
    """Coarse group name for an AIS ship-type code (tanker/cargo/passenger/…), or 'unknown'."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if c <= 0:
        return "unknown"
    for lo, hi, name in SHIP_TYPE_GROUPS:
        if lo <= c <= hi:
            return name
    return "other"


def subscribe_message(key, bbox=None):
    """AISStream subscribe frame. Asks for position reports AND static data so the hub can
    label vessels with their type, name and destination."""
    return {"APIKey": key, "BoundingBoxes": bbox or WORLD_BBOX,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def parse_position(msg):
    """AISStream PositionReport -> normalized position dict, or None.

    AIS sentinels are mapped to None: SOG >= 102.3 kn ('not available'), COG >= 360,
    TrueHeading 511 ('not available')."""
    if not isinstance(msg, dict) or msg.get("MessageType") != "PositionReport":
        return None
    pr = (msg.get("Message") or {}).get("PositionReport") or {}
    meta = msg.get("MetaData") or {}
    sog = _num(pr.get("Sog"))
    if sog is not None and sog >= 102.3:
        sog = None
    cog = _num(pr.get("Cog"))
    if cog is not None and cog >= 360:
        cog = None
    hdg = _num(pr.get("TrueHeading"))
    if hdg is not None and (hdg >= 360):
        hdg = None
    return {"mmsi": meta.get("MMSI"), "name": (meta.get("ShipName") or "").strip(),
            "lat": _num(pr.get("Latitude")), "lon": _num(pr.get("Longitude")),
            "sog": sog, "cog": cog, "heading": hdg,
            "nav_status": pr.get("NavigationalStatus")}


def parse_static(msg):
    """AISStream ShipStaticData -> {mmsi, name, ship_type, ship_type_group, destination,
    length, width, draught, callsign, imo}, or None. Forwarded verbatim as a
    'vessel_static' hub event; the gateway merges it onto positions by MMSI."""
    if not isinstance(msg, dict) or msg.get("MessageType") != "ShipStaticData":
        return None
    sd = (msg.get("Message") or {}).get("ShipStaticData") or {}
    meta = msg.get("MetaData") or {}
    mmsi = meta.get("MMSI") or sd.get("UserID")
    if mmsi is None:
        return None
    dim = sd.get("Dimension") or {}
    a, b = _num(dim.get("A")), _num(dim.get("B"))
    c, d = _num(dim.get("C")), _num(dim.get("D"))
    length = (a + b) if (a is not None and b is not None) else None
    width = (c + d) if (c is not None and d is not None) else None
    stype = sd.get("Type")
    name = (sd.get("Name") or meta.get("ShipName") or "").strip()
    dest = (sd.get("Destination") or "").strip() or None
    draught = _num(sd.get("MaximumStaticDraught"))
    imo = sd.get("ImoNumber")
    return {"mmsi": mmsi, "name": name or None, "ship_type": stype,
            "ship_type_group": type_group(stype), "destination": dest,
            "length": length, "width": width,
            "draught": draught if (draught and draught > 0) else None,
            "callsign": (sd.get("CallSign") or "").strip() or None,
            "imo": imo if imo else None}


async def stream_positions(bbox=None):
    """A plain one-connection generator for scratch scripts (does NOT reconnect)."""
    try:
        import websockets
    except ImportError as e:
        raise RuntimeError("pip install websockets") from e
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        raise RuntimeError("AISSTREAM_API_KEY not set — free at aisstream.io")
    async with websockets.connect(AIS_WS) as ws:
        await ws.send(json.dumps(subscribe_message(key, bbox)))
        async for raw in ws:
            m = json.loads(raw)
            v = parse_position(m) or parse_static(m)
            if v:
                yield v
