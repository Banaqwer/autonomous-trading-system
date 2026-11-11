"""Free data adapter backed by the public Yahoo Finance chart API.

Lets the whole engine run end-to-end with zero paid credentials. Only daily/
intraday OHLCV for equities/ETFs is supported; depth/options are not available
from this source (raise NotImplementedError), which is the honest free-tier limit.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from .base import Coverage, DataClient, Dataset, Schema, _utc

_INTERVAL = {
    Schema.OHLCV_1D: "1d",
    Schema.OHLCV_1H: "1h",
    Schema.OHLCV_1M: "1m",
}


class YahooDataClient:
    name = "yahoo"
    _BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def _fetch(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "openquant/0.1 (+research)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def get_bars(
        self,
        symbol: str,
        schema: Schema = Schema.OHLCV_1D,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        dataset: Dataset | None = None,
    ) -> pd.DataFrame:
        interval = _INTERVAL.get(schema)
        if interval is None:
            raise ValueError(f"Yahoo adapter supports {list(_INTERVAL)}, not {schema}")
        params = [f"interval={interval}", "events=div,splits"]
        if start or end:
            s = int(_utc(start).timestamp()) if start else 0
            e = int(_utc(end).timestamp()) if end else int(datetime.now(timezone.utc).timestamp())
            params += [f"period1={s}", f"period2={e}"]
        else:
            params.append("range=2y")
        url = f"{self._BASE}{symbol}?{'&'.join(params)}"
        data = self._fetch(url)
        res = data["chart"]["result"]
        if not res:
            raise ValueError(f"no data for {symbol}: {data['chart'].get('error')}")
        res = res[0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "close": q["close"],
                "volume": q["volume"],
            },
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        df.index.name = "ts_event"
        df["symbol"] = symbol.upper()
        return df.dropna(subset=["open", "high", "low", "close"])

    def get_coverage(self, symbol: str) -> Coverage:
        df = self.get_bars(symbol, Schema.OHLCV_1D)
        return Coverage(
            dataset="YAHOO",
            schema=Schema.OHLCV_1D.value,
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
            symbols=1,
        )

    def resolve_symbology(self, symbol: str) -> dict:
        return {"input": symbol, "resolved": symbol.upper(), "source": "yahoo"}
