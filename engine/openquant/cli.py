"""OpenQuant CLI: pull data -> backtest -> canonical trade log -> full grade.

    python -m openquant.cli backtest SPY --fast 20 --slow 50
    python -m openquant.cli sweep SPY
"""
from __future__ import annotations

import argparse
import json

from .data import Schema, get_data_client
from .backtest import Backtester, Breakout, MACrossover, run_grid
from .grade import (attribute_pnl_by_regime, grade_verdict, regime_aware_mc,
                    simulate_prop_firm)

STRATS = {"ma": MACrossover, "breakout": Breakout}


def _bars(symbol: str, schema: str):
    return get_data_client().get_bars(symbol, Schema(schema))


def cmd_backtest(a) -> None:
    bars = _bars(a.symbol, a.schema)
    strat = (MACrossover(a.fast, a.slow, allow_short=a.short)
             if a.strategy == "ma" else Breakout(a.fast, a.slow))
    res = Backtester(initial_equity=a.equity).run(bars, strat)
    reg = attribute_pnl_by_regime(res.trade_log, bars)
    verdict = grade_verdict(res, regime_attr=reg)
    mc = regime_aware_mc(res.trade_log.trade_pnls(), start_equity=a.equity)
    out = {
        "symbol": a.symbol, "strategy": strat.name, "params": res.params,
        "stats": res.stats(),
        "verdict": verdict.as_dict(),
        "regime": reg,
        "monte_carlo": mc.as_dict(),
    }
    if a.prop_firm:
        out["prop_firm"] = simulate_prop_firm(res).as_dict()
    print(json.dumps(out, indent=2, default=str))
    if a.csv:
        res.trade_log.to_csv(a.csv)
        print(f"\ncanonical trade log -> {a.csv}")


def cmd_sweep(a) -> None:
    bars = _bars(a.symbol, a.schema)
    grid = {"fast": [10, 20, 30, 50], "slow": [50, 100, 150, 200]}
    df = run_grid(bars, MACrossover, grid, initial_equity=a.equity)
    print(df.to_string(index=False))


def main() -> None:
    p = argparse.ArgumentParser(prog="openquant")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest")
    b.add_argument("symbol")
    b.add_argument("--strategy", choices=list(STRATS), default="ma")
    b.add_argument("--schema", default="ohlcv-1d")
    b.add_argument("--fast", type=int, default=20)
    b.add_argument("--slow", type=int, default=50)
    b.add_argument("--short", action="store_true")
    b.add_argument("--equity", type=float, default=100_000.0)
    b.add_argument("--prop-firm", action="store_true")
    b.add_argument("--csv", default=None)
    b.set_defaults(func=cmd_backtest)

    s = sub.add_parser("sweep")
    s.add_argument("symbol")
    s.add_argument("--schema", default="ohlcv-1d")
    s.add_argument("--equity", type=float, default=100_000.0)
    s.set_defaults(func=cmd_sweep)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
