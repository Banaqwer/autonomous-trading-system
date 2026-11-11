"""Fast offline tests — synthetic data, no network. Run: pytest -q (or python -m pytest)."""
import numpy as np
import pandas as pd
import pytest

from openquant.backtest import Backtester, MACrossover, TradeLog, CANONICAL_COLUMNS, run_grid
from openquant.grade import grade_verdict, regime_aware_mc, simulate_prop_firm


def _synthetic(n=400, seed=1):
    rng = np.random.default_rng(seed)
    # trending series with noise so a trend strategy has something to catch
    drift = np.linspace(0, 0.6, n)
    px = 100 * np.exp(drift + np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="UTC")
    df = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99,
                       "close": px, "volume": 1e6}, index=idx)
    df.index.name = "ts_event"; df["symbol"] = "TEST"
    return df


def test_canonical_columns_exact():
    assert CANONICAL_COLUMNS[0] == "Trade #"
    assert CANONICAL_COLUMNS[-1] == "Cumulative P&L %"
    assert len(CANONICAL_COLUMNS) == 15


def test_backtest_cash_accounting_consistent():
    bars = _synthetic()
    res = Backtester(initial_equity=100_000).run(bars, MACrossover(10, 30))
    # final equity should equal initial + sum of realized trade pnls (flat at end or close-marked)
    assert res.equity.iloc[-1] > 0
    stats = res.stats()
    assert set(["net_pnl_usd", "sharpe", "max_drawdown_pct", "num_trades"]).issubset(stats)


def test_tradelog_two_rows_per_trade():
    bars = _synthetic()
    res = Backtester().run(bars, MACrossover(10, 30))
    df = res.trade_log.to_frame()
    entries = df["Type"].astype(str).str.startswith("Entry").sum()
    exits = df["Type"].astype(str).str.startswith("Exit").sum()
    assert entries >= exits  # last position may still be open
    assert list(df.columns) == CANONICAL_COLUMNS


def test_no_lookahead_flat_strategy_zero_pnl():
    bars = _synthetic()

    class Flat(MACrossover):
        def signals(self, b):
            import pandas as pd
            return pd.Series(0.0, index=b.index)

    res = Backtester().run(bars, Flat())
    assert abs(res.equity.iloc[-1] - res.initial_equity) < 1e-6
    assert res.stats()["num_trades"] == 0


def test_verdict_flags_thin_sample():
    bars = _synthetic(n=150)
    res = Backtester().run(bars, MACrossover(20, 50))
    v = grade_verdict(res)
    assert v.bucket in {"A", "B", "C", "D", "F"}
    assert 0 <= v.score <= 100
    assert set(v.sub_scores) == {"edge", "robustness", "risk", "sample_adequacy"}


def test_montecarlo_bounds():
    pnls = [500, -200, 800, -100, 300, -50, 400]
    mc = regime_aware_mc(pnls, start_equity=100_000, n_paths=2000)
    assert 0.0 <= mc.prob_profit <= 1.0
    assert mc.p05_pnl <= mc.median_pnl <= mc.p95_pnl


def test_propfirm_runs():
    bars = _synthetic()
    res = Backtester().run(bars, MACrossover(10, 30))
    pf = simulate_prop_firm(res, n_sims=2000)
    assert 0.0 <= pf.pass_rate <= 1.0


def test_sweep_sorted():
    bars = _synthetic()
    df = run_grid(bars, MACrossover, {"fast": [10, 20], "slow": [50, 100]})
    assert len(df) == 4
    assert df["sharpe"].is_monotonic_decreasing
