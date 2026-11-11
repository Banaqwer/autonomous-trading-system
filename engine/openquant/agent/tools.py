"""Agent tool registry: the research capabilities the AI agent can call.

Each tool is a JSON-schema definition (for the Claude tool-use API) plus a
Python handler. The handlers wrap the OpenQuant engine, so the agent can pull
data, backtest, grade, sweep, run the prop-firm desk, and execute code — the
same tool surface the reverse-engineered agent exposes (data access, run_file,
backtest, verdict), minus the proprietary UI.
"""
from __future__ import annotations

import json

from ..data import Schema, get_data_client
from ..backtest import Backtester, MACrossover, Breakout, run_grid
from ..grade import (attribute_pnl_by_regime, grade_verdict, regime_aware_mc,
                     simulate_prop_firm, PropFirmSpec)
from .sandbox import run_python

_STRATS = {"ma_crossover": MACrossover, "breakout": Breakout}


def _bt(symbol, strategy, params, equity):
    bars = get_data_client().get_bars(symbol, Schema.OHLCV_1D)
    factory = _STRATS.get(strategy, MACrossover)
    strat = factory(**(params or {}))
    return bars, Backtester(initial_equity=equity).run(bars, strat)


# ---- handlers ----
def _h_get_bars(symbol, lookback_days=504, **_):
    df = get_data_client().get_bars(symbol, Schema.OHLCV_1D).tail(lookback_days)
    return {"symbol": symbol, "rows": len(df),
            "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
            "tail": df.tail(5)[["open", "high", "low", "close", "volume"]].round(2).reset_index().astype(str).to_dict("records")}


def _h_backtest(symbol, strategy="ma_crossover", params=None, equity=100_000.0, **_):
    _, res = _bt(symbol, strategy, params, equity)
    return {"symbol": symbol, "strategy": strategy, "params": res.params, "stats": res.stats()}


def _h_grade(symbol, strategy="ma_crossover", params=None, equity=100_000.0, **_):
    bars, res = _bt(symbol, strategy, params, equity)
    reg = attribute_pnl_by_regime(res.trade_log, bars)
    v = grade_verdict(res, regime_attr=reg)
    mc = regime_aware_mc(res.trade_log.trade_pnls(), start_equity=equity)
    return {"verdict": v.as_dict(), "regime": reg, "monte_carlo": mc.as_dict(), "stats": res.stats()}


def _h_sweep(symbol, fast=None, slow=None, equity=100_000.0, **_):
    bars = get_data_client().get_bars(symbol, Schema.OHLCV_1D)
    grid = {"fast": fast or [10, 20, 30, 50], "slow": slow or [50, 100, 150, 200]}
    df = run_grid(bars, MACrossover, grid, initial_equity=equity)
    return {"top": df.head(8).to_dict("records")}


def _h_prop_firm(symbol, strategy="ma_crossover", params=None, equity=100_000.0, spec=None, **_):
    _, res = _bt(symbol, strategy, params, equity)
    pf = simulate_prop_firm(res, PropFirmSpec(**(spec or {})))
    return pf.as_dict()


def _h_run_python(code, timeout=30, **_):
    return run_python(code, timeout=timeout)


HANDLERS = {
    "get_bars": _h_get_bars,
    "backtest": _h_backtest,
    "grade_strategy": _h_grade,
    "run_sweep": _h_sweep,
    "prop_firm_desk": _h_prop_firm,
    "run_python": _h_run_python,
}

TOOLS = [
    {"name": "get_bars", "description": "Fetch recent daily OHLCV bars for a symbol (futures root, stock, or ETF).",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "lookback_days": {"type": "integer"}},
      "required": ["symbol"]}},
    {"name": "backtest", "description": "Backtest a strategy and return performance stats.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"},
         "strategy": {"type": "string", "enum": list(_STRATS)},
         "params": {"type": "object"},
         "equity": {"type": "number"}}, "required": ["symbol"]}},
    {"name": "grade_strategy", "description": "Backtest then grade: Verdict (edge/robustness/risk/sample), regime attribution, regime-aware Monte Carlo.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "strategy": {"type": "string", "enum": list(_STRATS)},
         "params": {"type": "object"}, "equity": {"type": "number"}}, "required": ["symbol"]}},
    {"name": "run_sweep", "description": "Parameter sweep over fast/slow MA windows; returns the top configs by Sharpe.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "fast": {"type": "array", "items": {"type": "integer"}},
         "slow": {"type": "array", "items": {"type": "integer"}}}, "required": ["symbol"]}},
    {"name": "prop_firm_desk", "description": "Monte-Carlo a strategy against a prop-firm evaluation; returns pass rate, cost to funded, EV.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "strategy": {"type": "string"},
         "params": {"type": "object"}, "spec": {"type": "object"}}, "required": ["symbol"]}},
    {"name": "run_python", "description": "Execute Python in the sandbox (openquant is importable). Returns stdout/stderr/exit_code.",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["code"]}},
]


def dispatch(name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool {name}"}
    try:
        return handler(**args)
    except Exception as e:  # tools must never crash the loop
        return {"error": f"{type(e).__name__}: {e}"}
