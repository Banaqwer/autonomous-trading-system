"""Market-data abstraction layer.

The interface is deliberately shaped like the institutional-vendor API that
QuantPad white-labels (Databento): symbology, schemas, datasets and a
``get_range``-style call. This lets a free adapter (Yahoo) and a paid adapter
(Databento) sit behind one interface, so strategy/agent code never changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Protocol, runtime_checkable

import pandas as pd


class Schema(str, Enum):
    """Record granularities, mirroring the vendor schema names."""
    OHLCV_1S = "ohlcv-1s"
    OHLCV_1M = "ohlcv-1m"
    OHLCV_1H = "ohlcv-1h"
    OHLCV_1D = "ohlcv-1d"
    TRADES = "trades"
    MBP_1 = "mbp-1"      # top of book
    MBP_10 = "mbp-10"    # 10-level depth
    DEFINITION = "definition"
    STATISTICS = "statistics"


class Dataset(str, Enum):
    """Venue/feed identifiers, mirroring the vendor dataset codes."""
    CME_FUTURES = "GLBX.MDP3"
    NASDAQ_ITCH = "XNAS.ITCH"
    NASDAQ_BASIC = "XNAS.BASIC"
    NYSE_PILLAR = "XNYS.PILLAR"
    ARCA_PILLAR = "ARCX.PILLAR"
    OPRA_OPTIONS = "OPRA.PILLAR"


@dataclass(frozen=True)
class Bar:
    ts_event: datetime          # bar open time (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str

    @property
    def ts(self) -> datetime:
        return self.ts_event


@dataclass(frozen=True)
class Trade:
    ts_event: datetime
    price: float
    size: float
    side: str                   # 'A' ask-side aggressor, 'B' bid-side, 'N' none
    symbol: str


@dataclass(frozen=True)
class Quote:
    ts_event: datetime
    bid_px: float
    ask_px: float
    bid_sz: float
    ask_sz: float
    symbol: str


@dataclass
class Coverage:
    dataset: str
    schema: str
    start: datetime
    end: datetime
    symbols: int = 0


def _utc(dt: str | datetime) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(dt).replace(tzinfo=timezone.utc)


@runtime_checkable
class DataClient(Protocol):
    """Every adapter implements this. ``get_bars`` returns a tidy DataFrame
    indexed by UTC timestamp with columns open/high/low/close/volume."""

    name: str

    def get_bars(
        self,
        symbol: str,
        schema: Schema = Schema.OHLCV_1D,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        dataset: Dataset | None = None,
    ) -> pd.DataFrame: ...

    def get_coverage(self, symbol: str) -> Coverage: ...

    def resolve_symbology(self, symbol: str) -> dict: ...
