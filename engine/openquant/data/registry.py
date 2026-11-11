"""Pick a data adapter by name/env. Defaults to the free Yahoo adapter so the
engine always runs; set OPENQUANT_DATA=databento (with a key) for the paid path."""
from __future__ import annotations

import os

from .base import DataClient
from .yahoo import YahooDataClient


def get_data_client(name: str | None = None) -> DataClient:
    name = (name or os.environ.get("OPENQUANT_DATA") or "yahoo").lower()
    if name == "yahoo":
        return YahooDataClient()
    if name == "databento":
        from .databento_adapter import DatabentoDataClient
        return DatabentoDataClient()
    raise ValueError(f"unknown data adapter: {name}")
