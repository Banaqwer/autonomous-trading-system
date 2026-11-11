"""Equity/FX fallbacks & cross-checks for LSE: Alpha Vantage, Twelve Data, Tiingo, Finnhub.
Each free-tier; whichever key is set is tried in order. One quote() / candles() interface.
Keys: ALPHAVANTAGE_API_KEY, TWELVEDATA_API_KEY, TIINGO_API_KEY, FINNHUB_API_KEY.
"""
from __future__ import annotations
import os, json, urllib.request

def _j(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {"User-Agent":"openterm/0.1"}), timeout=25) as r:
        return json.loads(r.read())

def quote(symbol="AAPL"):
    if os.environ.get("FINNHUB_API_KEY"):
        d = _j(f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={os.environ['FINNHUB_API_KEY']}")
        return {"source": "finnhub", "symbol": symbol, "last": d.get("c"), "chg_pct": d.get("dp"),
                "high": d.get("h"), "low": d.get("l")}
    if os.environ.get("TWELVEDATA_API_KEY"):
        d = _j(f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={os.environ['TWELVEDATA_API_KEY']}")
        return {"source": "twelvedata", "symbol": symbol, "last": float(d.get("close",0) or 0),
                "chg_pct": float(d.get("percent_change",0) or 0)}
    if os.environ.get("TIINGO_API_KEY"):
        d = _j(f"https://api.tiingo.com/iex/?tickers={symbol}&token={os.environ['TIINGO_API_KEY']}")
        x = d[0] if d else {}
        return {"source": "tiingo", "symbol": symbol, "last": x.get("last"), "chg_pct": None}
    if os.environ.get("ALPHAVANTAGE_API_KEY"):
        d = _j(f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={os.environ['ALPHAVANTAGE_API_KEY']}")
        q = d.get("Global Quote", {})
        return {"source": "alphavantage", "symbol": symbol, "last": float(q.get("05. price",0) or 0),
                "chg_pct": float((q.get("10. change percent","0%") or "0%").rstrip("%"))}
    raise RuntimeError("no equity-fallback key set (ALPHAVANTAGE/TWELVEDATA/TIINGO/FINNHUB_API_KEY)")
