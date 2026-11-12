"""OPEN TERMINAL — data gateway.

One FastAPI app that feeds every terminal module from real open data, picking a
provider per asset class. No-key endpoints (crypto via Deribit/Binance, storms via
NOAA/NHC, factors via Kenneth French, satellite tiles via NASA GIBS) work out of
the box; key'd ones (LSE, FRED, AISStream) return a clear 503 until the key is set.

Run:  pip install -r requirements.txt
      uvicorn app:app --reload --port 8080
Then point the web terminal at http://localhost:8080  (set OT_API in the page).
"""
from __future__ import annotations
try:
    import config  # baked-in keys (gitignored)
except Exception:
    pass
import os, asyncio, contextlib, json, logging
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import stream_hub

from providers import (deribit, binance, nhc, french, gibs, lse, fred, openmeteo,
                       eia, edgar, gdelt, worldbank, comtrade, intl_macro, usda,
                       bakerhughes, equities_fallback, copernicus)

app = FastAPI(title="OPEN TERMINAL gateway", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CRYPTO = {"BTC", "ETH", "SOL", "BTCUSDT", "ETHUSDT", "SOLUSDT"}

def _crypto(sym): return sym.upper().replace("USDT", "") in {"BTC", "ETH", "SOL"}
def _pair(sym): return sym.upper() if sym.upper().endswith("USDT") else sym.upper() + "USDT"
def _need_key(fn):
    try:
        return fn()
    except RuntimeError as e:
        raise HTTPException(503, str(e))

@app.get("/api/health")
def health():
    watched = ("LSE_API_KEY", "FRED_API_KEY", "AISSTREAM_API_KEY", "EIA_API_KEY",
               "USDA_API_KEY", "FINNHUB_API_KEY", "TWELVEDATA_API_KEY",
               "TIINGO_API_KEY", "ALPHAVANTAGE_API_KEY", "SENTINELHUB_INSTANCE_ID")
    keys = {k: bool(os.environ.get(k)) for k in watched}
    stream = stream_hub.snapshot()
    return {"ok": True,
            "no_key_live": ["deribit", "binance", "nhc", "french", "gibs", "open-meteo",
                            "sec-edgar", "gdelt", "world-bank", "un-comtrade"],
            "keys_present": keys, "ws": "/ws",
            "stream": {"ok": stream["ok"],
                       "feeds": {n: f["state"] for n, f in stream["feeds"].items()}}}

# ---------------- merged live stream (/ws) ----------------
_clients: set[asyncio.Queue] = set()
_bg_tasks: set[asyncio.Task] = set()   # strong refs: asyncio only keeps weak ones, so an
                                       # unreferenced task can be garbage-collected mid-run
_log = logging.getLogger("uvicorn.error").getChild("gateway")

async def _forever(name, factory):
    """Keep a background coroutine alive: if it ever raises, log and restart it.
    A normal return means it has nothing left to do (e.g. every feed is
    permanently disabled); cancellation (shutdown) stops it."""
    while True:
        try:
            await factory()
            _log.warning("%s finished (nothing left to run)", name)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("%s crashed; restarting", name)
        await asyncio.sleep(1)

def _spawn(name, factory):
    t = asyncio.create_task(_forever(name, factory), name=name)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t

@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Start the merged stream on startup; cancel it cleanly on shutdown."""
    central: asyncio.Queue = asyncio.Queue()
    async def fanout():
        while True:
            ev = await central.get()
            for q in list(_clients):
                if q.qsize() < 500:        # slow client: shed its events, never block the rest
                    q.put_nowait(ev)
    _spawn("stream-hub", lambda: stream_hub.run_hub(central))
    _spawn("stream-fanout", fanout)
    try:
        yield
    finally:
        tasks = list(_bg_tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app.router.lifespan_context = _lifespan

@app.get("/api/stream_status")
def stream_status():
    """Per-feed health of the merged live stream (state, reconnects, last error,
    seconds since last message). `ok` is False if any enabled feed is not live."""
    return {**stream_hub.snapshot(), "clients": len(_clients)}

@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue()
    _clients.add(q)

    async def pump():                       # server -> client
        while True:
            await sock.send_text(json.dumps(await q.get()))

    async def watch():
        # Only receive() reports a close while nothing is being sent. Without it a
        # closed tab is never reaped when the stream is quiet (feeds down), and
        # uvicorn's graceful shutdown waits on this handler forever.
        while (await sock.receive())["type"] != "websocket.disconnect":
            pass                            # the page never sends; ignore anything it does

    tasks = [asyncio.create_task(pump()), asyncio.create_task(watch())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)   # also swallows send errors
        _clients.discard(q)

# ---- MON / CHRT / FOOT ----
@app.get("/api/candles")
def candles(symbol: str = "BTC", timeframe: str = "1h", limit: int = 300):
    if _crypto(symbol):
        return {"source": "binance", "symbol": symbol, "candles": binance.klines(_pair(symbol), timeframe, limit)}
    return {"source": "lse", "symbol": symbol, "candles": _need_key(lambda: lse.candles(symbol, timeframe, limit=limit))}

@app.get("/api/quote")
def quote(symbol: str = "BTC"):
    if _crypto(symbol):
        return {"source": "binance", **binance.ticker(_pair(symbol))}
    rows = _need_key(lambda: lse.candles(symbol, "1d", limit=2))
    return {"source": "lse", "symbol": symbol, "last": rows[-1]["close"] if rows else None}

# ---- BOOK / HEAT / tape ----
@app.get("/api/book")
def book(symbol: str = "BTC", limit: int = 50):
    if _crypto(symbol):
        return {"source": "binance", **binance.depth(_pair(symbol), limit)}
    raise HTTPException(501, "L2 book for this asset needs the LSE paid tick feed (data='book')")

@app.get("/api/trades")
def trades(symbol: str = "BTC", limit: int = 50):
    if _crypto(symbol):
        return {"source": "binance", "trades": binance.trades(_pair(symbol), limit)}
    raise HTTPException(501, "tape for this asset needs the LSE tick feed")

# ---- OPTS / VOLS / GREEKS / GEX ----
@app.get("/api/options")
def options(underlying: str = "BTC"):
    if _crypto(underlying):
        return {"source": "deribit", "chain": deribit.chain(underlying.upper().replace("USDT", ""))}
    return {"source": "lse", "chain": _need_key(lambda: lse.options(underlying))}

@app.get("/api/gex")
def gex(underlying: str = "BTC"):
    if _crypto(underlying):
        return {"source": "deribit", **deribit.gex(underlying.upper().replace("USDT", ""))}
    raise HTTPException(501, "equity GEX needs LSE options OI; wire lse.options() open interest")

# ---- WX ----
@app.get("/api/storms")
def storms():
    return {"source": "noaa-nhc", "storms": nhc.active_storms()}

# ---- WX (global weather beyond hurricanes) ----
@app.get("/api/weather")
def weather(lat: float = 29.3, lon: float = -94.8, hours: int = 24):
    return {"source": "open-meteo", "lat": lat, "lon": lon,
            "forecast": openmeteo.forecast(lat, lon, hours)}

@app.get("/api/marine")
def marine(lat: float = 27.0, lon: float = -90.0, hours: int = 24):
    return {"source": "open-meteo", "marine": openmeteo.marine(lat, lon, hours)}

# ---- SAT ----
@app.get("/api/satellite")
def satellite(layer: str = "truecolor", date: str = "default"):
    return {"source": "nasa-gibs", "layer": gibs.LAYERS.get(layer, layer),
            "tile_template": gibs.tile_template(gibs.LAYERS.get(layer, layer), date)}

@app.get("/api/satellite_image")
def satellite_image(bbox: str = "-96.80,35.95,-96.72,36.02", w: int = 1024, h: int = 1024):
    """Real Sentinel-2 true-color PNG (base64) for a bbox — Copernicus. Default: Cushing OK oil terminal."""
    from providers import copernicus
    try:
        b = [float(x) for x in bbox.split(",")]
    except Exception:
        raise HTTPException(400, "bbox must be w,s,e,n")
    return {"source": "copernicus-sentinel2", **copernicus.truecolor_png(b, w, h)}

# ---- NEWS / ECO / factors ----
@app.get("/api/factors")
def factors(dataset: str = "factors_monthly", limit: int = 360):
    return {"source": "ken-french", "dataset": dataset, "rows": french.factors(dataset, limit)}

@app.get("/api/macro")
def macro(series_id: str = "DCOILWTICO", limit: int = 120):
    return {"source": "fred", "series": series_id, "obs": _need_key(lambda: fred.series(series_id, limit))}

@app.get("/api/calendar")
def calendar(region: str = "US"):
    return {"source": "lse", "events": _need_key(lambda: lse.economic_calendar(region))}

# ---- energy / commodity fundamentals ----
@app.get("/api/eia")
def eia_series(series_id: str = "PET.WCESTUS1.W", length: int = 52):
    return {"source": "eia", "series": series_id, "obs": _need_key(lambda: eia.series(series_id, length))}

@app.get("/api/rigcount")
def rigcount():
    return {"source": "baker-hughes", **bakerhughes.rig_count()}

@app.get("/api/usda")
def usda_stats(commodity: str = "CORN", statistic: str = "PRODUCTION", year: str = "2024"):
    return {"source": "usda-nass", "rows": _need_key(lambda: usda.quickstats(commodity, statistic, year))}

# ---- news / filings ----
@app.get("/api/news")
def news(query: str = "crude oil OR OPEC", limit: int = 40):
    return {"source": "gdelt", "articles": gdelt.news(query, limit)}

@app.get("/api/filings")
def filings(q: str = "", forms: str = "8-K,10-Q,10-K"):
    return {"source": "sec-edgar", "filings": edgar.latest_filings(q, forms)}

# ---- international macro / trade (SPLC) ----
@app.get("/api/worldbank")
def wb(country: str = "US", code: str = "NY.GDP.MKTP.CD"):
    return {"source": "world-bank", "country": country, "code": code, "rows": worldbank.indicator(country, code)}

@app.get("/api/imf")
def imf(indicator: str = "NGDP_RPCH", country: str = "USA"):
    return {"source": "imf", "rows": intl_macro.imf(indicator, country)}

@app.get("/api/eurostat")
def eurostat(dataset: str = "prc_hicp_manr", params: str = "geo=EA&coicop=CP00"):
    return {"source": "eurostat", "rows": intl_macro.eurostat(dataset, params)}

@app.get("/api/oecd")
def oecd(flow: str = "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA"):
    return {"source": "oecd", **({"meta": intl_macro.oecd(flow)})}

@app.get("/api/trade")
def trade(reporter: str = "842", commodity: str = "2709", year: str = "2023", flow: str = "M"):
    return {"source": "un-comtrade", "flows": comtrade.flows(reporter, "all", commodity, year, flow)}

# ---- equity fallback / cross-check ----
@app.get("/api/equity_quote")
def equity_quote(symbol: str = "AAPL"):
    return _need_key(lambda: equities_fallback.quote(symbol))

# ---- higher-res satellite ----
@app.get("/api/sentinel")
def sentinel(layer: str = "TRUE-COLOR-S2L2A"):
    return {"source": "copernicus-sentinel2", **copernicus.wms_template(layer)}

# ---- engine: backtest / grade (reuses OpenQuant engine if installed) ----
@app.get("/api/grade")
def grade(symbol: str = "SPY", fast: int = 20, slow: int = 50):
    try:
        import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
        from openquant.data import get_data_client, Schema
        from openquant.backtest import Backtester, MACrossover
        from openquant.grade import grade_verdict, attribute_pnl_by_regime
        bars = get_data_client().get_bars(symbol, Schema.OHLCV_1D)
        res = Backtester().run(bars, MACrossover(fast, slow))
        v = grade_verdict(res, regime_attr=attribute_pnl_by_regime(res.trade_log, bars))
        return {"source": "openquant-engine", "symbol": symbol, "stats": res.stats(), "verdict": v.as_dict()}
    except Exception as e:
        raise HTTPException(500, f"engine not available: {e}")


# ---------------- serve the web terminal next to the gateway ----------------
# Mounted LAST so /api/* and /ws take precedence. Open http://localhost:8080/app/openterm.html
_WEB = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_WEB):
    @app.get("/")
    def _root():
        return RedirectResponse("/app/openterm.html")
    app.mount("/app", StaticFiles(directory=_WEB, html=True), name="web")
