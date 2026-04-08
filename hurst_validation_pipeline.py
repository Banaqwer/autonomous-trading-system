"""
═══════════════════════════════════════════════════════════════════════════════
HURST CYCLIC VALIDATION PIPELINE
True Hurst methodology backtest + Monte Carlo + Walk-Forward

Engine: hurst_cyclic_trading.py  (the real Hurst implementation)
        ├── CycleDetector       : FFT spectral analysis against nominal periods
        ├── HurstMovingAverages : half-span MA, full-span MA, inverse MA
        ├── EnvelopeEngine      : curvilinear upper/lower envelopes
        ├── HurstSignalEngine   : edge-band + mid-band timing
        └── HurstBacktester     : envelope-based stops, confluence scoring

Signal logic (from the book):
  BUY  : price crosses above lower envelope (edge-band)
         OR price crosses above half-span MA while it is rising (mid-band)
  SELL : price crosses below upper envelope (edge-band)
         OR price crosses below half-span MA while it is falling (mid-band)

Stop placement (from the book):
  Edge-band stop : lower_envelope - 0.5 × envelope_width
  Mid-band stop  : lower_envelope (the last cycle trough)
  → If price falls back below the cycle trough that triggered the trade,
    the cycle premise is broken. That is the stop.

Target (from the book):
  Upper envelope = projected next cycle peak

Thresholds — calibrated for Hurst cycle trading (not generic trend-following):
  Sharpe   > 0.6   Win Rate > 42%   Profit Factor > 1.4
  Max DD   > -35%  MC > 70%         WF ≥ 2/3 OOS positive
═══════════════════════════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import json
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

# ── Import the actual Hurst engine ────────────────────────────────────────────
from hurst_cyclic_trading import (
    CycleDetector,
    HurstMovingAverages,
    EnvelopeEngine,
    HurstSignalEngine,
    HurstBacktester,
    PerformanceReport,
    Side,
)

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
CONFIG = {
    "start_date"      : "2015-01-01",
    "end_date"        : "2024-12-31",
    "initial_capital" : 100_000,
    "risk_per_trade"  : 0.02,        # 2% risk per trade (same as live system)
    "min_bars"        : 300,         # need enough data for 18-month cycle detection
    "long_only"       : True,        # match live system (no shorting)

    # Transaction costs — applied on top of Hurst engine
    "costs": {
        "stock_etf"   : 0.001,       # 0.10% per side
        "crypto"      : 0.002,       # 0.20% per side (Kraken taker fee)
    },

    # Monte Carlo
    "mc_simulations"  : 1000,
    "mc_seed"         : 42,

    # Walk-Forward OOS splits
    "wf_splits": [
        ("2015-01-01", "2018-12-31", "2019-01-01", "2020-06-30"),
        ("2015-01-01", "2020-06-30", "2020-07-01", "2022-06-30"),
        ("2015-01-01", "2022-06-30", "2022-07-01", "2024-12-31"),
    ],

    # ── Pass thresholds — Hurst cycle system ──────────────────────────────
    # Better entry timing (cycle troughs) → better win rate than generic TF
    # Envelope-based stops → better defined risk
    "thresholds": {
        "sharpe"              : 0.6,
        "sortino"             : 0.8,
        "win_rate"            : 42.0,
        "profit_factor"       : 1.4,
        "max_drawdown"        : -35.0,
        "expectancy"          : 0.0,
        "min_trades"          : 12,      # Hurst systems trade less frequently
        "avg_r_multiple"      : 0.3,     # avg R per trade > 0.3
        "mc_profitable_pct"   : 70.0,
        "mc_median_sharpe"    : 0.4,
        "wf_positive_periods" : 2,
        # Benchmark: strategy must reduce max drawdown vs buy-and-hold
        "dd_reduction"        : 5.0,
    },

    "max_correlation" : 0.75,
}

# ── MARKET UNIVERSE ───────────────────────────────────────────────────────────
UNIVERSE = {
    "CRYPTO"        : ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
                       "ADA-USD", "AVAX-USD", "DOT-USD", "LINK-USD"],
    "MEGA_CAP_TECH" : ["NVDA", "AAPL", "MSFT", "GOOGL", "META",
                       "AMZN", "TSLA", "AMD", "AVGO", "ORCL", "NFLX"],
    "SEMICONDUCTORS": ["SOXX", "SMH", "QCOM", "MU", "AMAT",
                       "LRCX", "KLAC", "TXN", "MRVL"],
    "GROWTH_ETFs"   : ["QQQ", "IWF", "VUG", "TQQQ", "IGV",
                       "CIBR", "CLOU", "BOTZ"],
    "SECTOR_ETFs"   : ["XLK", "XLF", "XLE", "XLV", "XLI",
                       "XLY", "XLC", "XLB"],
    "COMMODITIES"   : ["GLD", "SLV", "PDBC", "CPER", "URA",
                       "REMX", "PICK", "FCX"],
    "MOMENTUM"      : ["CRM", "NOW", "SNOW", "PLTR", "COIN",
                       "MSTR", "HOOD", "SHOP"],
    "VALIDATED"     : ["SPY", "QQQ", "GLD", "BTC-USD", "ETH-USD"],
}

CRYPTO_TICKERS = {"BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD",
                  "ADA-USD","AVAX-USD","DOT-USD","LINK-USD","COIN","MSTR"}

# ── DATA ──────────────────────────────────────────────────────────────────────

def download_data(ticker, start, end):
    """Download adjusted close price data."""
    try:
        raw = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False)
        if raw.empty:
            return None
        raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
        df = raw[["Close"]].dropna()
        df.columns = ["Close"]
        df = df[df["Close"] > 0]
        return df if len(df) >= CONFIG["min_bars"] else None
    except Exception:
        return None


def get_cost(ticker):
    return CONFIG["costs"]["crypto" if ticker in CRYPTO_TICKERS else "stock_etf"]


# ── HURST BACKTEST WITH COSTS ─────────────────────────────────────────────────

def run_hurst_backtest(df, ticker=""):
    """
    Run the full Hurst pipeline on a price series.
    Applies transaction costs on top of the engine's trades.
    Long-only if CONFIG['long_only'] is True.
    Returns (trades_list, equity_curve, cycle_components, report_dict)
    """
    prices = df["Close"].values.astype(float)
    cost   = get_cost(ticker)

    # ── Step 1: Detect cycles ─────────────────────────────────────────────
    detector   = CycleDetector(prices)
    components = detector.detect_cycles()
    if not components:
        return None, None, None, None

    # ── Step 2: Generate Hurst signals ────────────────────────────────────
    engine  = HurstSignalEngine(prices, components)
    signals = engine.generate_signals()

    # Filter to long-only if configured
    if CONFIG["long_only"]:
        signals = [s for s in signals if s.side == Side.LONG]

    if len(signals) < 3:
        return None, None, components, None

    # ── Step 3: Apply transaction costs to stop/target ────────────────────
    # Widen stop slightly (buy at ask not mid) and reduce target slightly
    for sig in signals:
        sig.price = sig.price * (1 + cost)          # enter at ask
        # Stop and target unchanged — they are structural levels, not %

    # ── Step 4: Run backtest ──────────────────────────────────────────────
    backtester = HurstBacktester(
        prices=prices,
        signals=signals,
        risk_per_trade=CONFIG["risk_per_trade"],
        initial_capital=CONFIG["initial_capital"],
    )
    trades, equity_df = backtester.run()

    if not trades:
        return None, None, components, None

    # Apply exit cost to each completed trade's PnL
    for trade in trades:
        exit_cost = trade.exit_price * cost
        trade.pnl -= exit_cost * (trade.exit_price / trade.entry_price
                                  if trade.entry_price > 0 else 1)

    # ── Step 5: Generate performance report ──────────────────────────────
    report = PerformanceReport.generate(trades, equity_df, CONFIG["initial_capital"])
    report["n_cycles_detected"] = len(components)
    report["trading_cycle_period"] = round(
        engine.trading_cycle.period if engine.trading_cycle else 0, 1
    )
    report["avg_cycle_confidence"] = round(
        float(np.mean([c.confidence for c in components])), 3
    )

    return trades, equity_df, components, report


def calc_benchmark(prices, initial=100_000):
    """Buy-and-hold baseline for drawdown comparison."""
    equity   = initial * (prices / prices[0])
    ret      = (equity[-1] - initial) / initial * 100
    rets     = pd.Series(equity).pct_change().dropna()
    sharpe   = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    roll_max = np.maximum.accumulate(equity)
    max_dd   = ((equity - roll_max) / roll_max * 100).min()
    return {
        "bh_return" : round(ret, 2),
        "bh_sharpe" : round(sharpe, 3),
        "bh_max_dd" : round(max_dd, 2),
    }


def extract_metrics(trades, equity_df, initial=100_000):
    """
    Full metrics from trade list and equity curve.
    Augments PerformanceReport with Sortino, Calmar, max consec loss.
    """
    if not trades:
        return None
    eq  = equity_df["equity"].values
    rets = pd.Series(eq).pct_change().dropna()

    neg = rets[rets < 0]
    sortino = (rets.mean() / neg.std() * np.sqrt(252)
               if len(neg) > 0 and neg.std() > 0 else 0)

    roll_max = np.maximum.accumulate(eq)
    dd       = (eq - roll_max) / roll_max * 100
    max_dd   = dd.min()
    calmar   = ((eq[-1] - initial) / initial * 100 / abs(max_dd)
                if max_dd < 0 else 0)

    pnls   = [t.pnl for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(pnls)
    wr     = len(wins) / n * 100 if n > 0 else 0
    pf     = (sum(wins) / abs(sum(losses))
              if losses and sum(losses) != 0 else 99)
    exp    = (wr/100 * np.mean(wins) - (1-wr/100) * abs(np.mean(losses))
              if wins and losses else 0)
    avg_r  = np.mean([t.r_multiple for t in trades])

    # Max consecutive losses
    max_cl = cur = 0
    for p in pnls:
        cur = cur + 1 if p <= 0 else 0
        max_cl = max(max_cl, cur)

    sharpe_val = (rets.mean() / rets.std() * np.sqrt(252)
                  if rets.std() > 0 else 0)

    total_ret  = (eq[-1] - initial) / initial * 100

    return {
        "total_return"    : round(total_ret, 2),
        "final_capital"   : round(eq[-1], 2),
        "n_trades"        : n,
        "win_rate"        : round(wr, 2),
        "profit_factor"   : round(pf, 3),
        "expectancy"      : round(exp, 2),
        "avg_r_multiple"  : round(float(avg_r), 3),
        "sharpe"          : round(float(sharpe_val), 3),
        "sortino"         : round(float(sortino), 3),
        "calmar"          : round(float(calmar), 3),
        "max_drawdown"    : round(float(max_dd), 2),
        "max_consec_loss" : max_cl,
    }


# ── MONTE CARLO ───────────────────────────────────────────────────────────────

def run_monte_carlo(trades):
    """Bootstrap resample 1000 paths from the realized trade PnLs."""
    if len(trades) < 8:
        return None

    pnls  = [t.pnl for t in trades]
    n     = len(pnls)
    rng   = np.random.default_rng(CONFIG["mc_seed"])
    sims  = []

    for _ in range(CONFIG["mc_simulations"]):
        path    = rng.choice(pnls, size=n, replace=True)
        equity  = np.cumsum(path) + CONFIG["initial_capital"]
        equity  = np.insert(equity, 0, CONFIG["initial_capital"])
        final   = (equity[-1] - CONFIG["initial_capital"]) / CONFIG["initial_capital"] * 100
        roll    = np.maximum.accumulate(equity)
        max_dd  = ((equity - roll) / roll * 100).min()
        rets    = pd.Series(equity).pct_change().dropna()
        sharpe  = (rets.mean() / rets.std() * np.sqrt(252)
                   if rets.std() > 0 else 0)
        sims.append({"ret": final, "dd": max_dd, "sharpe": sharpe,
                     "profitable": final > 0})

    df = pd.DataFrame(sims)
    return {
        "profitable_pct"  : round(df["profitable"].mean() * 100, 1),
        "median_return"   : round(df["ret"].median(), 2),
        "worst_5pct"      : round(df["ret"].quantile(0.05), 2),
        "best_5pct"       : round(df["ret"].quantile(0.95), 2),
        "median_max_dd"   : round(df["dd"].median(), 2),
        "worst_dd_5pct"   : round(df["dd"].quantile(0.05), 2),
        "median_sharpe"   : round(df["sharpe"].median(), 3),
        "n_simulations"   : CONFIG["mc_simulations"],
    }


# ── WALK-FORWARD ──────────────────────────────────────────────────────────────

def run_walk_forward(df, ticker=""):
    """3-split OOS walk-forward test using the full Hurst engine on each period."""
    oos_results = []

    for _, _, test_s, test_e in CONFIG["wf_splits"]:
        test_df = df[test_s:test_e]
        if len(test_df) < CONFIG["min_bars"] // 2:
            continue

        trades, equity_df, components, _ = run_hurst_backtest(test_df, ticker)
        bh = calc_benchmark(test_df["Close"].values)

        if trades is None or len(trades) < 2:
            oos_results.append({
                "period"   : f"{test_s[:7]} → {test_e[:7]}",
                "return"   : 0.0, "sharpe": 0.0, "n_trades": 0,
                "positive" : False, "bh_return": bh["bh_return"],
                "cycles"   : len(components) if components else 0,
            })
            continue

        m = extract_metrics(trades, equity_df)
        if m:
            oos_results.append({
                "period"   : f"{test_s[:7]} → {test_e[:7]}",
                "return"   : m["total_return"],
                "sharpe"   : m["sharpe"],
                "n_trades" : m["n_trades"],
                "positive" : m["total_return"] > 0,
                "bh_return": bh["bh_return"],
                "cycles"   : len(components) if components else 0,
            })

    if not oos_results:
        return None

    pos   = sum(1 for r in oos_results if r["positive"])
    avg_r = np.mean([r["return"] for r in oos_results])

    return {
        "splits"          : oos_results,
        "positive_periods": pos,
        "total_periods"   : len(oos_results),
        "avg_oos_return"  : round(avg_r, 2),
        "consistency"     : round(pos / len(oos_results), 3),
    }


# ── PASS / FAIL ───────────────────────────────────────────────────────────────

def pass_fail(m, bh, mc, wf):
    """
    Hurst-calibrated pass/fail across 10 criteria.
    Returns (passed, failures, score, total, grade)
    """
    t      = CONFIG["thresholds"]
    failed = []
    score  = 0
    total  = 10

    # Backtest (7 criteria)
    if m["sharpe"]        >= t["sharpe"]:           score += 1
    else: failed.append(f"Sharpe {m['sharpe']:.2f} < {t['sharpe']}")

    if m["sortino"]       >= t["sortino"]:          score += 1
    else: failed.append(f"Sortino {m['sortino']:.2f} < {t['sortino']}")

    if m["win_rate"]      >= t["win_rate"]:         score += 1
    else: failed.append(f"WinRate {m['win_rate']:.1f}% < {t['win_rate']}%")

    if m["profit_factor"] >= t["profit_factor"]:    score += 1
    else: failed.append(f"PF {m['profit_factor']:.2f} < {t['profit_factor']}")

    if m["max_drawdown"]  >= t["max_drawdown"]:     score += 1
    else: failed.append(f"MaxDD {m['max_drawdown']:.1f}% worse than {t['max_drawdown']}%")

    if m["expectancy"]    >= t["expectancy"]:       score += 1
    else: failed.append(f"Expectancy ${m['expectancy']:.0f} < 0")

    if m["n_trades"]      >= t["min_trades"]:       score += 1
    else: failed.append(f"Only {m['n_trades']} trades (min {t['min_trades']})")

    # Benchmark: drawdown reduction (1 criterion)
    dd_improv = bh["bh_max_dd"] - m["max_drawdown"]   # positive = system has smaller DD
    if dd_improv >= t["dd_reduction"]:                  score += 1
    else: failed.append(f"DD not reduced: {m['max_drawdown']:.1f}% vs B&H {bh['bh_max_dd']:.1f}%")

    # Monte Carlo (1 criterion)
    if mc:
        if mc["profitable_pct"] >= t["mc_profitable_pct"]: score += 1
        else: failed.append(f"MC profitable {mc['profitable_pct']:.1f}% < {t['mc_profitable_pct']}%")
    else:
        failed.append("MC: insufficient trades")

    # Walk-Forward (1 criterion)
    if wf:
        if wf["positive_periods"] >= t["wf_positive_periods"]: score += 1
        else: failed.append(f"WF: {wf['positive_periods']}/{wf['total_periods']} OOS positive")
    else:
        failed.append("WF: insufficient data")

    passed = (len(failed) == 0)
    grade  = "A" if score >= 9 else "B" if score >= 7 else "C" if score >= 5 else "D"
    return passed, failed, score, total, grade


# ── SINGLE MARKET VALIDATOR ───────────────────────────────────────────────────

def validate_market(ticker, sector):
    """Run the full 4-pillar Hurst validation on one market."""
    df = download_data(ticker, CONFIG["start_date"], CONFIG["end_date"])
    if df is None:
        return {"ticker": ticker, "sector": sector, "status": "NO_DATA",
                "passed": False, "grade": "F", "score": "0/10"}

    prices = df["Close"].values

    # Full Hurst backtest
    trades, equity_df, components, report = run_hurst_backtest(df, ticker)
    if trades is None or report is None:
        return {"ticker": ticker, "sector": sector,
                "status": "NO_CYCLES" if components is not None else "INSUFFICIENT_TRADES",
                "passed": False, "grade": "F", "score": "0/10",
                "n_cycles": len(components) if components else 0}

    # Metrics
    m  = extract_metrics(trades, equity_df)
    if m is None:
        return {"ticker": ticker, "sector": sector, "status": "INSUFFICIENT_TRADES",
                "passed": False, "grade": "F", "score": "0/10"}

    # Add cycle info from Hurst engine
    m["n_cycles"]             = report.get("n_cycles_detected", 0)
    m["trading_cycle_period"] = report.get("trading_cycle_period", 0)
    m["avg_cycle_conf"]       = report.get("avg_cycle_confidence", 0)
    m["avg_r"]                = report.get("avg_r_multiple", m.get("avg_r_multiple", 0))

    # Buy-and-hold benchmark
    bh = calc_benchmark(prices)

    # Monte Carlo
    mc = run_monte_carlo(trades)

    # Walk-Forward
    wf = run_walk_forward(df, ticker)

    # Pass/Fail
    passed, failures, score, total, grade = pass_fail(m, bh, mc, wf)

    return {
        "ticker"     : ticker,
        "sector"     : sector,
        "passed"     : passed,
        "grade"      : grade,
        "score"      : f"{score}/{total}",
        "status"     : "PASS" if passed else "FAIL",
        "metrics"    : m,
        "benchmark"  : bh,
        "monte_carlo": mc,
        "walk_forward": wf,
        "failures"   : failures,
        "components" : [{"label": c.label, "period": round(c.period,1),
                         "confidence": round(c.confidence, 3)}
                        for c in (components or [])],
    }


# ── CORRELATION ───────────────────────────────────────────────────────────────

def build_correlation_matrix(passed_results):
    if len(passed_results) < 2:
        return None
    price_data = {}
    for r in passed_results:
        df = download_data(r["ticker"], CONFIG["start_date"], CONFIG["end_date"])
        if df is not None:
            price_data[r["ticker"]] = df["Close"].pct_change().dropna()
    if len(price_data) < 2:
        return None
    corr = pd.DataFrame(price_data).dropna().corr().round(3)
    tickers = list(corr.columns)
    pairs = [(tickers[i], tickers[j], corr.iloc[i,j])
             for i in range(len(tickers))
             for j in range(i+1, len(tickers))
             if abs(corr.iloc[i,j]) >= CONFIG["max_correlation"]]
    return {"matrix": corr, "high_corr_pairs": sorted(pairs, key=lambda x: abs(x[2]), reverse=True)}


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def run_hurst_validation():

    print("═" * 78)
    print("  HURST CYCLIC VALIDATION PIPELINE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Period   : {CONFIG['start_date']} → {CONFIG['end_date']}")
    print(f"  Engine   : Spectral cycle detection + envelope timing (hurst_cyclic_trading.py)")
    print(f"  Signals  : Edge-band + mid-band (Hurst book methodology)")
    print(f"  Stops    : Envelope-based structural stops (below cycle trough)")
    print(f"  Costs    : Stocks {CONFIG['costs']['stock_etf']*100:.2f}%/side | "
          f"Crypto {CONFIG['costs']['crypto']*100:.2f}%/side")
    print(f"  Mode     : {'Long-only' if CONFIG['long_only'] else 'Long + Short'}")
    print(f"  Criteria : Sharpe>{CONFIG['thresholds']['sharpe']} | "
          f"WR>{CONFIG['thresholds']['win_rate']}% | "
          f"PF>{CONFIG['thresholds']['profit_factor']} | "
          f"DD reduction>{CONFIG['thresholds']['dd_reduction']}%")
    print("═" * 78)

    # Build deduplicated market list
    seen, markets = set(), []
    for sector, tickers in UNIVERSE.items():
        for t in tickers:
            if t not in seen:
                seen.add(t)
                markets.append((t, sector))

    total   = len(markets)
    results = []

    print(f"\n  Running Hurst validation on {total} markets...\n")
    print(f"  {'#':>4}  {'Ticker':<12} {'Sector':<20}  Result")
    print(f"  {'─'*70}")

    for i, (ticker, sector) in enumerate(markets, 1):
        result = validate_market(ticker, sector)

        if result["passed"]:
            m  = result["metrics"]
            bh = result["benchmark"]
            status_str = (f"✅ PASS [Grade {result['grade']}] "
                          f"Sharpe {m['sharpe']:.2f} | "
                          f"WR {m['win_rate']:.0f}% | "
                          f"Cycles {m['n_cycles']} | "
                          f"DD {m['max_drawdown']:.1f}% vs B&H {bh['bh_max_dd']:.1f}%")
        else:
            top = result.get("failures", ["?"])[:1]
            status_str = f"❌ {result['status']}  {top[0] if top else ''}"

        print(f"  [{i:>3}/{total}]  {ticker:<12} {sector:<20}  {status_str}")
        results.append(result)

    # ── SUMMARY TABLE ─────────────────────────────────────────────────────
    passed_list  = [r for r in results if r["passed"]]
    failed_list  = [r for r in results if not r["passed"]]
    passed_sorted = sorted(passed_list,
                           key=lambda x: x["metrics"]["sharpe"], reverse=True)

    print(f"\n{'═'*78}")
    print(f"  VALIDATION COMPLETE")
    print(f"{'═'*78}")
    print(f"  Total    : {total}")
    print(f"  PASSED   : {len(passed_list)} ({len(passed_list)/total*100:.0f}%)")
    print(f"  FAILED   : {len(failed_list)} ({len(failed_list)/total*100:.0f}%)")

    if passed_sorted:
        print(f"\n{'═'*78}")
        print(f"  ✅  VALIDATED MARKETS — HURST CYCLE SYSTEM READY")
        print(f"{'═'*78}")
        print(f"  {'Ticker':<10} {'Sector':<18} Gr {'Score':<5} "
              f"{'Sharpe':>7} {'WR%':>6} {'PF':>5} {'DD%':>7} "
              f"{'MC%':>5} {'WF':>4} {'Cycles':>7} {'CycPer':>7}")
        print(f"  {'─'*75}")

        for r in passed_sorted:
            m  = r["metrics"]
            mc = r["monte_carlo"] or {}
            wf = r["walk_forward"] or {}
            print(f"  {r['ticker']:<10} {r['sector']:<18}  {r['grade']}  "
                  f"{r['score']:<5} "
                  f"{m['sharpe']:>7.2f} "
                  f"{m['win_rate']:>5.1f}% "
                  f"{m['profit_factor']:>5.2f} "
                  f"{m['max_drawdown']:>6.1f}% "
                  f"{mc.get('profitable_pct',0):>4.0f}% "
                  f"{wf.get('positive_periods',0)}/{wf.get('total_periods',3)} "
                  f"{m['n_cycles']:>7} "
                  f"{m['trading_cycle_period']:>7.0f}d")

    # ── DETAILED BREAKDOWN ────────────────────────────────────────────────
    if passed_sorted:
        print(f"\n{'═'*78}")
        print(f"  DETAILED METRICS — VALIDATED MARKETS")
        print(f"{'═'*78}")

        for r in passed_sorted:
            m   = r["metrics"]
            bh  = r["benchmark"]
            mc  = r["monte_carlo"] or {}
            wf  = r["walk_forward"] or {}
            comps = r.get("components", [])

            print(f"\n  ── {r['ticker']} ({r['sector']}) — Grade {r['grade']} [{r['score']}] ──")

            print(f"     HURST CYCLE DETECTION")
            for c in comps:
                conf_bar = "█" * int(c["confidence"] * 10)
                print(f"       {c['label']:>12}: period={c['period']:6.1f}d  "
                      f"confidence={c['confidence']:.2f} [{conf_bar:<10}]")

            print(f"     BACKTEST (envelope stops, {get_cost(r['ticker'])*200:.2f}% round-trip cost)")
            print(f"       Return         : {m['total_return']:+.2f}%")
            print(f"       Sharpe         : {m['sharpe']:.3f}")
            print(f"       Sortino        : {m['sortino']:.3f}")
            print(f"       Calmar         : {m['calmar']:.3f}")
            print(f"       Win Rate       : {m['win_rate']:.1f}%")
            print(f"       Profit Factor  : {m['profit_factor']:.2f}x")
            print(f"       Avg R-Multiple : {m['avg_r_multiple']:.2f}R")
            print(f"       Expectancy     : ${m['expectancy']:,.0f} per trade")
            print(f"       Max Drawdown   : {m['max_drawdown']:.1f}%")
            print(f"       Max Consec Loss: {m['max_consec_loss']} trades")
            print(f"       # Trades       : {m['n_trades']}")

            print(f"     BUY-AND-HOLD BENCHMARK")
            dd_improv = bh["bh_max_dd"] - m["max_drawdown"]
            print(f"       B&H Return     : {bh['bh_return']:+.2f}%")
            print(f"       B&H Sharpe     : {bh['bh_sharpe']:.3f}")
            print(f"       B&H Max DD     : {bh['bh_max_dd']:.1f}%")
            print(f"       DD Reduction   : {dd_improv:+.1f}% (system vs B&H)")

            if mc:
                print(f"     MONTE CARLO ({mc['n_simulations']} simulations, seed={CONFIG['mc_seed']})")
                print(f"       Profitable     : {mc['profitable_pct']:.1f}%")
                print(f"       Median Return  : {mc['median_return']:+.1f}%")
                print(f"       Worst 5%       : {mc['worst_5pct']:+.1f}%")
                print(f"       Worst DD 5%    : {mc['worst_dd_5pct']:.1f}%")
                print(f"       Median Sharpe  : {mc['median_sharpe']:.3f}")

            if wf:
                print(f"     WALK-FORWARD (OOS periods only)")
                for split in wf.get("splits", []):
                    flag = "✓" if split["positive"] else "✗"
                    print(f"       {flag} {split['period']}: "
                          f"{split['return']:+.1f}% | "
                          f"Sharpe {split['sharpe']:.2f} | "
                          f"{split['n_trades']} trades | "
                          f"{split['cycles']} cycles detected")
                print(f"       OOS Avg Return : {wf['avg_oos_return']:+.1f}%")
                print(f"       Consistency    : {wf['consistency']*100:.0f}%")

    # ── CORRELATION ───────────────────────────────────────────────────────
    if len(passed_sorted) >= 2:
        print(f"\n{'═'*78}")
        print(f"  CORRELATION ANALYSIS — PORTFOLIO CONSTRUCTION")
        print(f"{'═'*78}")
        corr_data = build_correlation_matrix(passed_sorted)
        if corr_data:
            mat   = corr_data["matrix"]
            ticks = list(mat.columns)
            hdr   = f"  {'':12}" + "".join(f"{t:>10}" for t in ticks)
            print(f"\n{hdr}")
            for t in ticks:
                row = "".join(
                    f"{'  ──':>10}" if t == t2 else f"{mat.loc[t,t2]:>10.2f}"
                    for t2 in ticks
                )
                print(f"  {t:<12}{row}")
            if corr_data["high_corr_pairs"]:
                print(f"\n  ⚠️  HIGH CORRELATION PAIRS (>{CONFIG['max_correlation']}):")
                for t1, t2, c in corr_data["high_corr_pairs"]:
                    print(f"     {t1} ↔ {t2} : {c:.2f}")
            else:
                print(f"\n  ✅ All pairs below {CONFIG['max_correlation']} — good diversification")

    # ── FAILED SUMMARY ────────────────────────────────────────────────────
    print(f"\n{'═'*78}")
    print(f"  ❌ FAILED / SKIPPED MARKETS")
    print(f"{'═'*78}")
    for r in sorted(failed_list, key=lambda x: int(x.get("score","0/10").split("/")[0]), reverse=True):
        if r["status"] in ("NO_DATA", "NO_CYCLES", "INSUFFICIENT_TRADES"):
            print(f"  {r['ticker']:<10}  {r['status']}")
        else:
            m    = r.get("metrics") or {}
            top  = r.get("failures", ["?"])[:2]
            print(f"  {r['ticker']:<10}  Score {r.get('score','?'):<6} | "
                  f"Sharpe {m.get('sharpe',0):.2f} | "
                  f"Cycles {m.get('n_cycles',0)} | "
                  f"{'; '.join(top)}")

    # ── SAVE OUTPUTS ──────────────────────────────────────────────────────
    out  = Path("validation_results")
    out.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")

    rows = []
    for r in results:
        m  = r.get("metrics") or {}
        bh = r.get("benchmark") or {}
        mc = r.get("monte_carlo") or {}
        wf = r.get("walk_forward") or {}
        rows.append({
            "ticker"           : r["ticker"],
            "sector"           : r["sector"],
            "passed"           : r["passed"],
            "grade"            : r["grade"],
            "score"            : r.get("score",""),
            "n_cycles"         : m.get("n_cycles",""),
            "trading_cycle_d"  : m.get("trading_cycle_period",""),
            "avg_cycle_conf"   : m.get("avg_cycle_conf",""),
            "sharpe"           : m.get("sharpe",""),
            "sortino"          : m.get("sortino",""),
            "win_rate"         : m.get("win_rate",""),
            "profit_factor"    : m.get("profit_factor",""),
            "avg_r_multiple"   : m.get("avg_r_multiple",""),
            "max_drawdown"     : m.get("max_drawdown",""),
            "total_return"     : m.get("total_return",""),
            "n_trades"         : m.get("n_trades",""),
            "max_consec_loss"  : m.get("max_consec_loss",""),
            "bh_sharpe"        : bh.get("bh_sharpe",""),
            "bh_max_dd"        : bh.get("bh_max_dd",""),
            "mc_profitable"    : mc.get("profitable_pct",""),
            "mc_median_sharpe" : mc.get("median_sharpe",""),
            "mc_worst_5pct"    : mc.get("worst_5pct",""),
            "wf_consistency"   : wf.get("consistency",""),
            "wf_avg_return"    : wf.get("avg_oos_return",""),
        })

    csv_path  = out / f"hurst_validation_{ts}.csv"
    json_path = out / f"hurst_validation_{ts}.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    validated_path = None
    if passed_sorted:
        validated_path = out / f"hurst_validated_markets_{ts}.txt"
        with open(validated_path, "w") as f:
            f.write("# Markets validated by Hurst Cyclic Pipeline\n")
            f.write(f"# Generated  : {datetime.now()}\n")
            f.write(f"# Engine     : Spectral cycle detection + envelope timing\n")
            f.write(f"# Stops      : Envelope-based (below cycle trough)\n\n")
            for r in passed_sorted:
                m = r["metrics"]
                f.write(f"{r['ticker']:<12} Grade={r['grade']}  "
                        f"Sharpe={m['sharpe']:.2f}  "
                        f"WR={m['win_rate']:.1f}%  "
                        f"Cycles={m['n_cycles']}  "
                        f"CyclePeriod={m['trading_cycle_period']:.0f}d\n")

    print(f"\n{'═'*78}")
    print(f"  OUTPUT FILES")
    print(f"{'═'*78}")
    print(f"  CSV    : {csv_path}")
    print(f"  JSON   : {json_path}")
    if validated_path:
        print(f"  List   : {validated_path}")
    print(f"\n  Paste results here → will update trading_config.py")
    print(f"{'═'*78}\n")

    return passed_sorted


if __name__ == "__main__":
    validated = run_hurst_validation()
