"""Feed real bars into the OpenQuant engine. LSE for stocks/FX/indices/commodities
(your key), Deribit/Binance-style crypto via the engine's Yahoo fallback, or Yahoo
for anything else. Returns the OHLCV DataFrame the engine's Backtester expects.
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "terminal", "backend"))
try:
    import config  # baked keys (shared with the terminal backend)  # noqa
except Exception:
    pass

# terminal FX symbol -> LSE underscore form
FX = {"EURUSD": "EUR_USD", "USDJPY": "USD_JPY", "GBPUSD": "GBP_USD", "AUDUSD": "AUD_USD"}
# futures -> Yahoo continuous front-month (free bars; no Databento needed for bars)
FUT = {"CL": "CL=F", "ES": "ES=F", "NQ": "NQ=F", "GC": "GC=F", "NG": "NG=F",
       "HG": "HG=F", "RB": "RB=F", "SI": "SI=F", "ZC": "ZC=F", "ZW": "ZW=F", "ZS": "ZS=F"}

def _df(rows, symbol):
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"no bars for {symbol}")
    df["ts_event"] = pd.to_datetime(df["timestamp"] if "timestamp" in df else df["date"], utc=True)
    df = df.set_index("ts_event").sort_index()
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c])
    df["volume"] = pd.to_numeric(df.get("volume", 0)).fillna(0)
    df["symbol"] = symbol
    return df[["open", "high", "low", "close", "volume", "symbol"]]

def get_bars(symbol: str, timeframe: str = "1d", limit: int = 400) -> pd.DataFrame:
    sym = symbol.upper()
    if sym in FUT:                                  # free futures bars via Yahoo continuous front-month
        from openquant.data import get_data_client, Schema
        df = get_data_client("yahoo").get_bars(FUT[sym], Schema.OHLCV_1D)
        df["symbol"] = sym
        return df
    if os.environ.get("LSE_API_KEY"):
        try:
            from lse import LSE
            q = FX.get(sym, symbol)
            with LSE() as c:
                rows = c.candles(q, timeframe, limit=limit)
            if rows:
                return _df(rows, sym)
        except Exception:
            pass
    # fallback: engine's free Yahoo adapter (equities/ETFs)
    from openquant.data import get_data_client, Schema
    return get_data_client("yahoo").get_bars(symbol, Schema.OHLCV_1D)
