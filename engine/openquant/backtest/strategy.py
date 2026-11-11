"""Strategy interface + a couple of reference strategies.

A Strategy sees the full bar history up to `i` and returns a target position in
{-1, 0, +1} (short / flat / long). The backtester handles sizing, fills, costs
and the trade log. Keep strategies pure and vectorizable-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


class Strategy:
    name = "base"

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        """Return a Series (index = bars.index) of target positions in {-1,0,1}."""
        raise NotImplementedError


@dataclass
class MACrossover(Strategy):
    fast: int = 20
    slow: int = 50
    allow_short: bool = False
    name: str = "ma_crossover"

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        c = bars["close"]
        f = c.rolling(self.fast).mean()
        s = c.rolling(self.slow).mean()
        pos = pd.Series(0, index=bars.index, dtype=float)
        pos[f > s] = 1.0
        if self.allow_short:
            pos[f < s] = -1.0
        return pos.fillna(0.0)


@dataclass
class Breakout(Strategy):
    lookback: int = 55
    exit_lookback: int = 20
    name: str = "breakout"

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        c = bars["close"]
        hi = c.rolling(self.lookback).max()
        lo = c.rolling(self.exit_lookback).min()
        pos = pd.Series(np.nan, index=bars.index, dtype=float)
        pos[c >= hi] = 1.0
        pos[c <= lo] = 0.0
        return pos.ffill().fillna(0.0)
