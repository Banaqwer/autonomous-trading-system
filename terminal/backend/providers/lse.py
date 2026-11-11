"""London Strategic Edge (LSE Data 3.0) — the unified open-data backbone.
Wraps the official `lse-data` SDK (`pip install lse-data`, `from lse import LSE`).
Covers stocks/FX/crypto/commodities/indices/ETFs/options+greeks/bond yields/macro.
Needs a free key: set LSE_API_KEY (get one at londonstrategicedge.com/data).
REST host download.londonstrategicedge.com/v3 (X-API-Key); WS data-ws.londonstrategicedge.com.
"""
from __future__ import annotations
import os

def _client():
    try:
        from lse import LSE
    except ImportError as e:
        raise RuntimeError("pip install lse-data  (and set LSE_API_KEY)") from e
    if not os.environ.get("LSE_API_KEY"):
        raise RuntimeError("LSE_API_KEY not set — get a free key at londonstrategicedge.com/data")
    return LSE()  # reads LSE_API_KEY from env

def candles(symbol, timeframe="1d", start=None, end=None, limit=500):
    with _client() as c:
        return c.candles(symbol, timeframe, start=start, end=end, limit=limit)

def options(underlying, type=None, max_dte=60):
    with _client() as c:
        kw = {"max_dte": max_dte}
        if type: kw["type"] = type
        return c.options(underlying, **kw)          # rows carry iv, delta, gamma, theta, vega

def options_flow(underlying=None, min_premium=100_000, limit=200):
    with _client() as c:
        return (c.options_flow(underlying, min_premium=min_premium)
                if underlying else c.options_flow(min_premium=min_premium, limit=limit))

def series(code, start=None):
    with _client() as c:
        return c.series(code, start=start) if start else c.series(code)   # macro + bond yields

def economic_calendar(region="US", start=None):
    with _client() as c:
        return c.economic_calendar(region=region, start=start)

def markets():
    with _client() as c:
        return c.markets()

def stream(symbols, on_tick):
    """Live quotes: calls on_tick(tick) for each. Blocks; run in a thread/task."""
    with _client() as c:
        c.on("tick", on_tick)
        c.connect(symbols)
