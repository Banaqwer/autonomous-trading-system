"""Market-regime detection and per-trade P&L attribution.

Each day is labelled by (trend, volatility): trend from price vs a long moving
average and its slope; volatility from realized vol against its rolling median.
Every closed trade is attributed to the regime in force at its entry, so P&L
concentration in one regime (a fragility signal) becomes measurable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TREND = {1: "bull", 0: "range", -1: "bear"}
VOL = {1: "high", 0: "low"}


def detect_regimes(bars: pd.DataFrame, ma: int = 100, vol_window: int = 20) -> pd.Series:
    c = bars["close"].astype(float)
    mavg = c.rolling(ma, min_periods=ma // 2).mean()
    slope = mavg.diff(5)
    trend = pd.Series(0, index=c.index)
    trend[(c > mavg) & (slope > 0)] = 1
    trend[(c < mavg) & (slope < 0)] = -1
    rv = c.pct_change().rolling(vol_window).std()
    hi = rv > rv.rolling(vol_window * 5, min_periods=vol_window).median()
    label = [f"{TREND[int(t)]}-{VOL[int(h)]}" for t, h in
             zip(trend.fillna(0), hi.fillna(False).astype(int))]
    return pd.Series(label, index=c.index, name="regime")


def attribute_pnl_by_regime(trade_log, bars: pd.DataFrame, ma: int = 100) -> dict:
    regimes = detect_regimes(bars, ma=ma)
    rows = [r for r in trade_log.rows if str(r["Type"]).startswith("Entry")]
    exits = [r for r in trade_log.rows if str(r["Type"]).startswith("Exit")]
    by: dict[str, float] = {}
    counts: dict[str, int] = {}
    for entry, ex in zip(rows, exits):
        ts = pd.Timestamp(entry["Date and time"])
        pos = regimes.index.get_indexer([ts], method="pad")[0]
        reg = regimes.iloc[pos] if pos >= 0 else "unknown"
        by[reg] = by.get(reg, 0.0) + float(ex["Net P&L USD"])
        counts[reg] = counts.get(reg, 0) + 1
    total = sum(by.values()) or 1.0
    top = max(by, key=lambda k: abs(by[k])) if by else None
    return {
        "pnl_by_regime": {k: round(v, 2) for k, v in sorted(by.items(), key=lambda x: -x[1])},
        "trades_by_regime": counts,
        "top_regime": top,
        "top_regime_pnl_share": round(by[top] / total, 3) if top else 0.0,
        "num_regimes_traded": len(by),
    }
