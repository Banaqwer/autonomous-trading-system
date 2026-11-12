"""MARKETS foundation routes (FB): /api/universe /api/quotes /api/quote /api/candles
/api/status, plus the backward-compatible /api/health and /api/stream_status.

All routes are plain `def` (threadpool). Nothing here calls an upstream directly:
quotes come from core.quotes (one poller + TTL cache), candles from core.candles
(TTL cache per id/tf).
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Query

import stream_hub
from core import VERSION, cache, candles as candle_core, session, status as status_board, universe
from core.fanout import HUB
from core.quotes import SERVICE

router = APIRouter()

# friendly name -> every environment variable that must be set for it to count as present
KEYS = {
    "ALPACA": ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"),
    "TIINGO": ("TIINGO_API_KEY",), "FRED": ("FRED_API_KEY",), "LSE": ("LSE_API_KEY",),
    "AISSTREAM": ("AISSTREAM_API_KEY",), "MARKETAUX": ("MARKETAUX_API_KEY",),
    "EIA": ("EIA_API_KEY",), "NASS": ("NASS_API_KEY",), "USDA_MARS": ("USDA_MARS_API_KEY",),
    "CDSE": ("CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET"), "BLS": ("BLS_API_KEY",),
    "FMP": ("FMP_API_KEY",), "ALPHAVANTAGE": ("ALPHAVANTAGE_API_KEY",),
    "FIRMS": ("FIRMS_MAP_KEY",), "CENSUS": ("CENSUS_API_KEY",), "OPENFDA": ("OPENFDA_API_KEY",),
    "DATA_GOV": ("DATA_GOV_API_KEY",), "DOL": ("DOL_API_KEY",),
    "LABORCURRENT": ("LABORCURRENT_API_KEY",), "API_NINJAS": ("API_NINJAS_KEY",),
    "NCEI": ("NCEI_TOKEN",), "OPENAQ": ("OPENAQ_API_KEY",),
    "COURTLISTENER": ("COURTLISTENER_API_TOKEN",),
}


def keys_present() -> dict:
    """Presence booleans only — never a value."""
    return {name: all(bool(os.environ.get(v)) for v in env) for name, env in KEYS.items()}


@router.get("/api/universe")
def universe_():
    """The 23 instruments: names, units, decimals, capabilities, why-not texts, source chains."""
    return universe.payload()


@router.get("/api/quotes")
def quotes():
    """All 23 quotes. Served from the quote service (refreshes only what is due)."""
    return SERVICE.snapshot()


@router.get("/api/quote")
def quote(id: str = Query(..., min_length=1, max_length=16, description="terminal id, e.g. CL")):
    return SERVICE.quote(id)


@router.get("/api/candles")
def candles(id: str = Query(..., min_length=1, max_length=16),
            tf: str = Query("1h", max_length=4, description="1m | 5m | 15m | 1h | 1d | 1w"),
            limit: int = Query(candle_core.DEFAULT_LIMIT, description="clamped to 1..1000")):
    return candle_core.get(id, tf, limit)


def _feeds() -> dict:
    out = {}
    for name, f in stream_hub.snapshot()["feeds"].items():
        out[name] = {"state": f["state"], "healthy": f["healthy"], "age": f["seconds_since_event"],
                     "connects": f["connects"], "reconnects": f["reconnects"],
                     "events": f["events"], "dropped_events": f["dropped_events"],
                     "last_error": f["last_error"], "last_error_at": f["last_error_at"],
                     "retry_in": f["retry_in"]}
    return out


@router.get("/api/status")
def status():
    """Honest health: `ok` is true only when at least one quote is currently fresh."""
    SERVICE.touch()                      # a page watching the status is a page showing quotes
    q = SERVICE.health()
    return {
        "ok": bool(q["ok"]), "server_time": time.time(), "version": VERSION,
        "clients": len(HUB.clients), "feeds": _feeds(), "quotes": q,
        "sources": status_board.snapshot(), "keys": keys_present(),
        "session": {"tz": session.tz_available(), "tz_source": session.tz_source()},
        "cache": cache.stats(),
        "ws": {"hub_events": HUB.stats["hub_events"], "sent": HUB.stats["sent"],
               "shed": sum(c.shed for c in list(HUB.clients))},
    }


@router.get("/api/health")
def health():
    """Liveness (kept for older clients): the process answers and where its stream is.
    For real data health use /api/status."""
    stream = stream_hub.snapshot()
    watched = sorted({v for env in KEYS.values() for v in env})
    return {"ok": True, "version": VERSION, "ws": "/ws", "status": "/api/status",
            "keys_present": {k: bool(os.environ.get(k)) for k in watched},
            "stream": {"ok": stream["ok"],
                       "feeds": {n: f["state"] for n, f in stream["feeds"].items()}}}


@router.get("/api/stream_status")
def stream_status():
    """Per-feed health of the merged live stream (state, reconnects, last error,
    seconds since last message). `ok` is False if any enabled feed is not live."""
    return {**stream_hub.snapshot(), "clients": len(HUB.clients)}
