"""AISStream.io — live global AIS vessel tracking for the SHIP module.
Free key: set AISSTREAM_API_KEY (aisstream.io). WebSocket wss://stream.aisstream.io/v0/stream.
The subscribe message MUST be sent within 3s of connecting. Keep the key server-side.

`AIS_WS`, `subscribe_message()` and `parse_position()` are the protocol pieces;
stream_hub.py uses them inside its supervised (auto-reconnecting) session.
`stream_positions()` is a plain one-connection async generator for scripts —
it does NOT reconnect; requires `pip install websockets`.
"""
from __future__ import annotations
import os, json

AIS_WS = "wss://stream.aisstream.io/v0/stream"


def subscribe_message(key, bbox=None):
    bbox = bbox or [[[-180, -90], [180, 90]]]     # whole globe; narrow for less volume
    return {"APIKey": key, "BoundingBoxes": bbox, "FilterMessageTypes": ["PositionReport"]}


def parse_position(msg):
    """AISStream message -> normalized position dict, or None if it isn't one."""
    if not isinstance(msg, dict) or msg.get("MessageType") != "PositionReport":
        return None
    pr = msg["Message"]["PositionReport"]
    meta = msg.get("MetaData") or {}
    return {"mmsi": meta.get("MMSI"), "name": (meta.get("ShipName") or "").strip(),
            "lat": pr.get("Latitude"), "lon": pr.get("Longitude"),
            "sog": pr.get("Sog"), "cog": pr.get("Cog"), "heading": pr.get("TrueHeading")}


async def stream_positions(bbox=None):
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
            v = parse_position(json.loads(raw))
            if v:
                yield v
