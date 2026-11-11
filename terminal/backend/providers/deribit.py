"""Deribit public API — crypto options chain, IV surface, per-strike greeks, GEX.
No API key required. JSON-RPC-over-HTTP GET.  Docs: docs.deribit.com
"""
from __future__ import annotations
import time, math, urllib.request, json

BASE = "https://www.deribit.com/api/v2"

def _get(method: str, **params) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/public/{method}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["result"]

def instruments(currency="BTC", kind="option"):
    return _get("get_instruments", currency=currency, kind=kind, expired="false")

def book_summary(currency="BTC", kind="option"):
    """Whole chain in one call: mark_iv, open_interest, underlying_price, volume."""
    return _get("get_book_summary_by_currency", currency=currency, kind=kind)

def ticker(instrument_name):
    """Per-instrument greeks (delta/gamma/vega/theta/rho) + mark_iv + index_price."""
    return _get("ticker", instrument_name=instrument_name)

def index_price(currency="BTC"):
    return _get("get_index_price", index_name=f"{currency.lower()}_usd").get("index_price")

def chain(currency="BTC", max_expiries=4):
    """Build a tidy options chain: rows of {expiry, strike, type, iv, oi, mark, underlying}."""
    insts = {i["instrument_name"]: i for i in instruments(currency)}
    rows = []
    for b in book_summary(currency):
        name = b["instrument_name"]
        meta = insts.get(name)
        if not meta:
            continue
        rows.append({
            "instrument": name,
            "expiry": meta["expiration_timestamp"],
            "strike": meta["strike"],
            "type": meta["option_type"],
            "iv": b.get("mark_iv"),
            "open_interest": b.get("open_interest"),
            "mark": b.get("mark_price"),
            "underlying": b.get("underlying_price"),
            "volume": b.get("volume"),
        })
    exps = sorted({r["expiry"] for r in rows})[:max_expiries]
    return [r for r in rows if r["expiry"] in exps]

def gex(currency="BTC"):
    """Dealer gamma exposure by strike: GEX = gamma * OI * S^2 * 0.01, puts negative.
    Uses chain IV + Black-Scholes gamma (fast, avoids a ticker call per strike)."""
    rows = chain(currency)
    if not rows:
        return {"spot": None, "strikes": [], "gex": []}
    S = next((r["underlying"] for r in rows if r["underlying"]), None)
    now = time.time() * 1000
    agg = {}
    for r in rows:
        K, iv, oi = r["strike"], (r["iv"] or 0) / 100.0, (r["open_interest"] or 0)
        if not (S and K and iv and oi):
            continue
        T = max((r["expiry"] - now) / (1000 * 86400 * 365), 1e-4)
        d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
        gamma = math.exp(-d1 * d1 / 2) / (2.5066 * S * iv * math.sqrt(T))
        g = gamma * oi * S * S * 0.01 / 1e6
        agg[K] = agg.get(K, 0) + (g if r["type"] == "call" else -g)
    ks = sorted(agg)
    return {"spot": S, "strikes": ks, "gex": [round(agg[k], 3) for k in ks]}
