"""AISStream.io — live global AIS vessel tracking for the SHIP module.
Free key: set AISSTREAM_API_KEY (aisstream.io). WebSocket wss://stream.aisstream.io/v0/stream.
The subscribe message MUST be sent within 3s of connecting. Keep the key server-side.
Requires `pip install websockets`; this is an async generator of position reports.
"""
from __future__ import annotations
import os, json

async def stream_positions(bbox=None):
    try:
        import websockets
    except ImportError as e:
        raise RuntimeError("pip install websockets") from e
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        raise RuntimeError("AISSTREAM_API_KEY not set — free at aisstream.io")
    bbox = bbox or [[[-180, -90], [180, 90]]]     # whole globe; narrow for less volume
    async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
        await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": bbox,
                                  "FilterMessageTypes": ["PositionReport"]}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("MessageType") == "PositionReport":
                pr = msg["Message"]["PositionReport"]
                meta = msg["MetaData"]
                yield {"mmsi": meta.get("MMSI"), "name": (meta.get("ShipName") or "").strip(),
                       "lat": pr.get("Latitude"), "lon": pr.get("Longitude"),
                       "sog": pr.get("Sog"), "cog": pr.get("Cog"), "heading": pr.get("TrueHeading")}
