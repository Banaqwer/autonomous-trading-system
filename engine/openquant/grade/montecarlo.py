"""Regime-aware Monte Carlo on the trade sequence.

Standard bootstrap resamples trades i.i.d.; the regime-aware variant resamples
*within* each regime bucket and reassembles in the observed regime order, so the
simulated paths preserve how the strategy behaves across market conditions
rather than assuming every trade is exchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MCResult:
    n_paths: int
    prob_profit: float
    median_pnl: float
    p05_pnl: float
    p95_pnl: float
    median_max_drawdown: float
    worst_max_drawdown: float
    risk_of_ruin: float           # P(equity ever drops below -ruin_frac * start)

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _paths_from_trades(trade_pnls: np.ndarray, regimes: list[str] | None,
                       n_paths: int, rng: np.random.Generator) -> np.ndarray:
    n = len(trade_pnls)
    if regimes is not None and len(regimes) == n and len(set(regimes)) > 1:
        buckets: dict[str, np.ndarray] = {}
        for r in set(regimes):
            buckets[r] = trade_pnls[np.array([x == r for x in regimes])]
        order = regimes
        out = np.empty((n_paths, n))
        for j, r in enumerate(order):
            b = buckets[r]
            out[:, j] = rng.choice(b, size=n_paths, replace=True)
        return out
    return rng.choice(trade_pnls, size=(n_paths, n), replace=True)


def regime_aware_mc(
    trade_pnls,
    start_equity: float = 100_000.0,
    regimes: list[str] | None = None,
    n_paths: int = 10_000,
    ruin_frac: float = 0.5,
    seed: int = 7,
) -> MCResult:
    pnls = np.asarray([p for p in trade_pnls], dtype=float)
    if len(pnls) == 0:
        return MCResult(n_paths, 0, 0, 0, 0, 0, 0, 1.0)
    rng = np.random.default_rng(seed)
    sampled = _paths_from_trades(pnls, regimes, n_paths, rng)
    equity = start_equity + np.cumsum(sampled, axis=1)
    final_pnl = equity[:, -1] - start_equity
    running_peak = np.maximum.accumulate(np.column_stack([np.full(n_paths, start_equity), equity]), axis=1)
    dd = (np.column_stack([np.full(n_paths, start_equity), equity]) - running_peak) / running_peak
    max_dd = dd.min(axis=1)
    ruin = (equity.min(axis=1) < start_equity * (1 - ruin_frac)).mean()
    return MCResult(
        n_paths=n_paths,
        prob_profit=float((final_pnl > 0).mean()),
        median_pnl=float(np.median(final_pnl)),
        p05_pnl=float(np.percentile(final_pnl, 5)),
        p95_pnl=float(np.percentile(final_pnl, 95)),
        median_max_drawdown=float(np.median(max_dd) * 100),
        worst_max_drawdown=float(max_dd.min() * 100),
        risk_of_ruin=float(ruin),
    )
