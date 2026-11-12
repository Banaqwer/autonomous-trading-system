"""FLOW slice (S2): /api/book /api/trades.

Moved verbatim from the old app.py by the foundation; the slice owner rewrites it
against REBUILD_SPEC §3.7 (ids instead of symbols, core.cache, error envelopes).
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

from providers import binance

router = APIRouter()


def _crypto(sym): return sym.upper().replace("USDT", "") in {"BTC", "ETH", "SOL"}
def _pair(sym): return sym.upper() if sym.upper().endswith("USDT") else sym.upper() + "USDT"


@router.get("/api/book")
def book(symbol: str = "BTC", limit: int = 50):
    if _crypto(symbol):
        return {"source": "binance", **binance.depth(_pair(symbol), limit)}
    raise HTTPException(501, "L2 book for this asset needs the LSE paid tick feed (data='book')")


@router.get("/api/trades")
def trades(symbol: str = "BTC", limit: int = 50):
    if _crypto(symbol):
        return {"source": "binance", "trades": binance.trades(_pair(symbol), limit)}
    raise HTTPException(501, "tape for this asset needs the LSE tick feed")
