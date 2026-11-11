"""Binance public spot API — klines (CHRT/FOOT), depth (BOOK/HEAT), aggTrades (tape).
No API key required.  Base: api.binance.com
"""
from __future__ import annotations
import urllib.request, json

BASE = "https://data-api.binance.vision/api/v3"  # public market-data mirror, no geo block; swap to api.binance.com where permitted

def _get(path, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

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
