"""Merged market stream — fan in multiple push feeds, emit one normalized stream.

Replaces REST polling: subscribes to Binance combined streams (aggTrade/depth/kline)
and Deribit ticker channels, normalizes to a common event, and relays to any number
of browser clients over a single server WebSocket (/ws in app.py).

Normalized event shapes:
  {"type":"trade","src":"binance","sym":"BTCUSDT","px":..,"qty":..,"buy":bool,"t":ms}
  {"type":"book", "src":"binance","sym":"BTCUSDT","bids":[[p,q]...],"asks":[[p,q]...]}
  {"type":"kline","src":"binance","sym":"BTCUSDT","o":..,"h":..,"l":..,"c":..,"v":..,"t":s}
  {"type":"ticker","src":"deribit","sym":"BTC-PERPETUAL","px":..,"iv":..,"t":ms}
"""
from __future__ import annotations
try:
    import config  # baked-in keys (gitignored)
except Exception:
    pass
import asyncio, json

BINANCE_WS = "wss://data-stream.binance.vision/stream?streams={streams}"
DERIBIT_WS = "wss://www.deribit.com/ws/api/v2"

async def binance_feed(symbols, out: asyncio.Queue):
    import websockets
    streams = "/".join(f"{s.lower()}@aggTrade/{s.lower()}@depth20@100ms/{s.lower()}@kline_1m" for s in symbols)
    url = BINANCE_WS.format(streams=streams)
    async for ws in _reconnect(url):
        async for raw in ws:
            m = json.loads(raw); d = m.get("data", {}); st = m.get("stream", "")
            if "@aggTrade" in st:
                await out.put({"type":"trade","src":"binance","sym":d["s"],"px":float(d["p"]),
                               "qty":float(d["q"]),"buy":not d["m"],"t":d["T"]})
            elif "@depth" in st:
                await out.put({"type":"book","src":"binance","sym":st.split("@")[0].upper(),
                               "bids":[[float(p),float(q)] for p,q in d["bids"]],
                               "asks":[[float(p),float(q)] for p,q in d["asks"]]})
            elif "@kline" in st:
                k=d["k"]; await out.put({"type":"kline","src":"binance","sym":d["s"],"o":float(k["o"]),
                               "h":float(k["h"]),"l":float(k["l"]),"c":float(k["c"]),"v":float(k["v"]),"t":k["t"]//1000})

async def deribit_feed(channels, out: asyncio.Queue):
    import websockets
    async for ws in _reconnect(DERIBIT_WS):
        await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"public/subscribe",
                                  "params":{"channels":channels}}))
        async for raw in ws:
            m = json.loads(raw)
            if m.get("method") == "subscription":
                p = m["params"]["data"]; ch = m["params"]["channel"]
                await out.put({"type":"ticker","src":"deribit","sym":p.get("instrument_name"),
                               "px":p.get("mark_price") or p.get("last_price"),
                               "iv":p.get("mark_iv"),"t":p.get("timestamp")})

async def _reconnect(url):
    import websockets
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2**22) as ws:
                yield ws
        except Exception:
            await asyncio.sleep(2)

async def ais_feed(out: asyncio.Queue, bbox=None):
    """Live vessels from AISStream (needs AISSTREAM_API_KEY). Normalized to
    {"type":"vessel","src":"ais","mmsi":..,"name":..,"lat":..,"lon":..,"sog":..,"cog":..}."""
    import os
    if not os.environ.get("AISSTREAM_API_KEY"):
        return
    from providers import ais
    while True:
        try:
            async for v in ais.stream_positions(bbox):
                if v.get("lat") is None or v.get("lon") is None:
                    continue
                await out.put({"type":"vessel","src":"ais","mmsi":v.get("mmsi"),
                               "name":v.get("name"),"lat":v.get("lat"),"lon":v.get("lon"),
                               "sog":v.get("sog"),"cog":v.get("cog")})
        except Exception:
            await asyncio.sleep(3)   # reconnect on drop

async def run_hub(out: asyncio.Queue, binance_syms=("BTCUSDT","ETHUSDT"),
                  deribit_chs=("ticker.BTC-PERPETUAL.100ms","ticker.ETH-PERPETUAL.100ms")):
    await asyncio.gather(binance_feed(list(binance_syms), out),
                         deribit_feed(list(deribit_chs), out),
                         ais_feed(out))   # AIS joins only if its key is set
