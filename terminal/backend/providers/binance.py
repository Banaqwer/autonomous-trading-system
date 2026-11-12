"""Binance public spot market data — keyless (owner: FB).

Base: data-api.binance.vision (the public market-data mirror, no geo block).
Spot BTC/ETH/SOL vs USDT. Real time. Weights are small (klines 2, depth 5-50,
tradingDay 4 per symbol) against 6000/min, but everything still goes through
core.cache in the routes.

New normalised API (REBUILD_SPEC shapes)
    candles(symbol, tf, limit)        -> [{t, o, h, l, c, v}]   t = bar open, epoch s
    trading_day(symbols)              -> {sym: {last, open, high, low, volume, open_time, close_time}}
                                         calendar day starting 00:00 UTC (the crypto "previous close")
    book_ticker(symbols)              -> {sym: {bid, ask, bid_qty, ask_qty}}
    ticker_24h(symbols)               -> {sym: {last, chg_pct_24h, high_24h, low_24h, volume_24h, …}}
    depth_snapshot(symbol, limit)     -> {bids:[[p,q]…], asks:[[p,q]…], last_update_id}
    agg_trades(symbol, limit, …)      -> [{id, t (epoch s, float), px, qty, buy}]  ascending

Legacy API (old shapes, still used by the routes moved out of app.py)
    klines / depth / trades / ticker
"""
from __future__ import annotations

import json

from core import http
from core.util import num

BASE = "https://data-api.binance.vision/api/v3"
SOURCE = "binance"
INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1w": "1w"}
DEPTH_LIMITS = (5, 10, 20, 50, 100, 500, 1000, 5000)


def _get(path: str, timeout: float = 10, **params):
    query = http.qs(params)
    return http.get_json(f"{BASE}/{path}" + (f"?{query}" if query else ""), timeout=timeout, source=SOURCE)


def _dicts(raw) -> list:
    """The ticker rows of a reply ([{…}] or {…}); anything else is not a Binance reply."""
    from core.errors import UpstreamError
    rows = raw if isinstance(raw, list) else [raw]
    if not rows or not all(isinstance(t, dict) for t in rows):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (expected ticker objects)")
    return rows


def _symbols(symbols) -> str:
    return json.dumps([s.upper() for s in symbols], separators=(",", ":"))


# ------------------------------------------------------------- normalised API
def parse_klines(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list) or not all(isinstance(k, list) and len(k) >= 6 for k in raw):
        from core.errors import UpstreamError
        raise UpstreamError(SOURCE, None, "unexpected reply shape (expected kline rows)")
    for k in raw:
        t, o, h, l, c, v = num(k[0]), num(k[1]), num(k[2]), num(k[3]), num(k[4]), num(k[5])
        if t is None or None in (o, h, l, c):
            continue
        out.append({"t": int(t) // 1000, "o": o, "h": h, "l": l, "c": c, "v": v or 0.0})
    return out


def candles(symbol: str, tf: str = "1h", limit: int = 300, start_ms: int | None = None) -> list[dict]:
    return parse_klines(_get("klines", symbol=symbol.upper(), interval=INTERVAL[tf],
                             limit=max(1, min(int(limit), 1000)), startTime=start_ms))


def parse_trading_day(raw) -> dict:
    out = {}
    for t in _dicts(raw):
        last = num(t.get("lastPrice"))
        if last is None or not t.get("symbol"):
            continue
        out[t["symbol"]] = {
            "last": last, "open": num(t.get("openPrice")), "high": num(t.get("highPrice")),
            "low": num(t.get("lowPrice")), "volume": num(t.get("volume")),
            "open_time": (num(t.get("openTime")) or 0) / 1000 or None,
            "close_time": (num(t.get("closeTime")) or 0) / 1000 or None}
    return out


def trading_day(symbols, timeout: float = 10) -> dict:
    """Calendar-day ticker (00:00 UTC open): the honest "day change" for crypto."""
    return parse_trading_day(_get("ticker/tradingDay", timeout, symbols=_symbols(symbols)))


def parse_book_ticker(raw) -> dict:
    out = {}
    for t in _dicts(raw):
        bid, ask = num(t.get("bidPrice")), num(t.get("askPrice"))
        if not t.get("symbol") or bid is None or ask is None:
            continue
        out[t["symbol"]] = {"bid": bid, "ask": ask,
                            "bid_qty": num(t.get("bidQty")), "ask_qty": num(t.get("askQty"))}
    return out


def book_ticker(symbols, timeout: float = 10) -> dict:
    return parse_book_ticker(_get("ticker/bookTicker", timeout, symbols=_symbols(symbols)))


def parse_ticker_24h(raw) -> dict:
    out = {}
    for t in _dicts(raw):
        if not t.get("symbol") or num(t.get("lastPrice")) is None:
            continue
        out[t["symbol"]] = {
            "last": num(t.get("lastPrice")), "chg_pct_24h": num(t.get("priceChangePercent")),
            "chg_24h": num(t.get("priceChange")), "bid": num(t.get("bidPrice")),
            "ask": num(t.get("askPrice")), "open_24h": num(t.get("openPrice")),
            "high_24h": num(t.get("highPrice")), "low_24h": num(t.get("lowPrice")),
            "volume_24h": num(t.get("volume")), "quote_volume_24h": num(t.get("quoteVolume")),
            "close_time": (num(t.get("closeTime")) or 0) / 1000 or None}
    return out


def ticker_24h(symbols) -> dict:
    """Rolling 24-hour statistics (NOT the day change: see trading_day)."""
    return parse_ticker_24h(_get("ticker/24hr", symbols=_symbols(symbols)))


def parse_depth(raw) -> dict:
    return {"bids": [[float(p), float(q)] for p, q in raw.get("bids") or []],
            "asks": [[float(p), float(q)] for p, q in raw.get("asks") or []],
            "last_update_id": raw.get("lastUpdateId")}


def depth_snapshot(symbol: str, limit: int = 100) -> dict:
    limit = max(1, min(int(limit), 5000))
    return parse_depth(_get("depth", symbol=symbol.upper(), limit=limit))


def parse_agg_trades(raw) -> list[dict]:
    out = []
    for t in raw or []:
        px, qty = num(t.get("p")), num(t.get("q"))
        if px is None or qty is None or t.get("T") is None:
            continue
        out.append({"id": t.get("a"), "t": t["T"] / 1000, "px": px, "qty": qty, "buy": not t.get("m")})
    out.sort(key=lambda r: (r["t"], r["id"] or 0))
    return out


def agg_trades(symbol: str, limit: int = 500, start_ms: int | None = None,
               end_ms: int | None = None, from_id: int | None = None) -> list[dict]:
    return parse_agg_trades(_get("aggTrades", symbol=symbol.upper(),
                                 limit=max(1, min(int(limit), 1000)),
                                 startTime=start_ms, endTime=end_ms, fromId=from_id))


# ------------------------------------------------------------------ legacy API
def klines(symbol="BTCUSDT", interval="1h", limit=300):
    raw = _get("klines", symbol=symbol, interval=interval, limit=limit)
    return [{"time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "vol": float(k[5])} for k in raw]


def depth(symbol="BTCUSDT", limit=50):
    d = _get("depth", symbol=symbol, limit=limit)
    return {"bids": [[float(p), float(q)] for p, q in d["bids"]],
            "asks": [[float(p), float(q)] for p, q in d["asks"]]}


def trades(symbol="BTCUSDT", limit=50):
    raw = _get("aggTrades", symbol=symbol, limit=limit)
    return [{"price": float(t["p"]), "qty": float(t["q"]),
             "time": t["T"] // 1000, "buy": not t["m"]} for t in raw]


def ticker(symbol="BTCUSDT"):
    t = _get("ticker/24hr", symbol=symbol)
    return {"symbol": symbol, "last": float(t["lastPrice"]),
            "chg_pct": float(t["priceChangePercent"]),
            "bid": float(t["bidPrice"]), "ask": float(t["askPrice"]),
            "high": float(t["highPrice"]), "low": float(t["lowPrice"]),
            "volume": float(t["volume"])}
