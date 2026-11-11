"""Paid-vendor adapter (Databento). Guarded: only used when the `databento`
package and a DATABENTO_API_KEY are present. This is the production data path
QuantPad uses; here it is an optional drop-in behind the same DataClient
interface. No key is required to run OpenQuant (the Yahoo adapter is default).
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

from .base import Coverage, Dataset, Schema, _utc


class DatabentoDataClient:
    name = "databento"

    def __init__(self, api_key: str | None = None):
        try:
            import databento as db  # noqa: F401
        except ImportError as e:  # pragma: no cover - optional dep
            raise RuntimeError(
                "pip install databento and set DATABENTO_API_KEY to use the paid adapter"
            ) from e
        self._db = db
        key = api_key or os.environ.get("DATABENTO_API_KEY")
        if not key:
            raise RuntimeError("DATABENTO_API_KEY not set")
        self._client = db.Historical(key)

    def get_bars(
        self,
        symbol: str,
        schema: Schema = Schema.OHLCV_1D,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        dataset: Dataset | None = None,
    ) -> pd.DataFrame:
        ds = (dataset or Dataset.CME_FUTURES).value
        store = self._client.timeseries.get_range(
            dataset=ds,
            symbols=[symbol],
            schema=schema.value,
            start=_utc(start) if start else None,
            end=_utc(end) if end else None,
            stype_in="continuous" if "." in symbol else "raw_symbol",
        )
        df = store.to_df()
        df = df.rename(columns=str.lower)
        if "ts_event" in df.columns:
            df = df.set_index("ts_event")
        df["symbol"] = symbol
        return df

    def get_coverage(self, symbol: str) -> Coverage:  # pragma: no cover - needs key
        raise NotImplementedError("use self._client.metadata.get_dataset_range")

    def resolve_symbology(self, symbol: str) -> dict:  # pragma: no cover
        return {"input": symbol, "resolved": symbol, "source": "databento"}
