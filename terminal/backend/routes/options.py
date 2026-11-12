"""OPTIONS slice (S3): /api/options* /api/gex /api/vol_surface.

Moved verbatim from the old app.py by the foundation; the slice owner rewrites it
against REBUILD_SPEC §3.7.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

from providers import deribit, lse

router = APIRouter()


def _crypto(sym): return sym.upper().replace("USDT", "") in {"BTC", "ETH", "SOL"}
def _need_key(fn):
    try:
        return fn()
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@router.get("/api/options")
def options(underlying: str = "BTC"):
    if _crypto(underlying):
        return {"source": "deribit", "chain": deribit.chain(underlying.upper().replace("USDT", ""))}
    return {"source": "lse", "chain": _need_key(lambda: lse.options(underlying))}


@router.get("/api/gex")
def gex(underlying: str = "BTC"):
    if _crypto(underlying):
        return {"source": "deribit", **deribit.gex(underlying.upper().replace("USDT", ""))}
    raise HTTPException(501, "equity GEX needs LSE options OI; wire lse.options() open interest")
