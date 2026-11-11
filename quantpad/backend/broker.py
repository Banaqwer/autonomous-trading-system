"""Paper-trade execution (optional). Human-approved proposals only — never
autonomous live trading. Uses Alpaca's paper REST API if keys are present.
Set APCA_API_KEY_ID + APCA_API_SECRET_KEY (free paper account at alpaca.markets).
"""
from __future__ import annotations
import os, json, urllib.request

PAPER = "https://paper-api.alpaca.markets/v2/orders"

def enabled():
    return bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))

def place_paper(symbol, side, qty=1):
    """side: 'buy' for LONG, 'sell' for SHORT. Equities/ETF/crypto only (no futures on Alpaca)."""
    if not enabled():
        return {"status": "no_broker", "note": "set APCA_API_KEY_ID / APCA_API_SECRET_KEY for paper trading"}
    body = json.dumps({"symbol": symbol, "qty": qty, "side": side,
                       "type": "market", "time_in_force": "day"}).encode()
    req = urllib.request.Request(PAPER, data=body, method="POST", headers={
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        return {"status": "submitted", "id": d.get("id"), "symbol": symbol, "side": side, "qty": qty}
    except Exception as e:
        return {"status": "error", "error": str(e)[:160], "symbol": symbol}
