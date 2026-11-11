"""Parameter sweep: run a strategy across a grid, return a tidy results frame.
Mirrors the 'sweep / run_grid' module used for the large parameter sweeps."""
from __future__ import annotations

import itertools
from typing import Callable

import pandas as pd

from .engine import Backtester
from .strategy import Strategy


def run_grid(
    bars: pd.DataFrame,
    strategy_factory: Callable[..., Strategy],
    grid: dict[str, list],
    initial_equity: float = 100_000.0,
) -> pd.DataFrame:
    keys = list(grid)
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        strat = strategy_factory(**params)
        res = Backtester(initial_equity=initial_equity).run(bars, strat)
        rows.append({**params, **res.stats()})
    df = pd.DataFrame(rows)
    sort_col = "sharpe" if "sharpe" in df.columns else df.columns[-1]
    return df.sort_values(sort_col, ascending=False).reset_index(drop=True)
