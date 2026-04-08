"""
Multi-Asset Backtest: NVDA + SOXX using Hurst Cyclic Trading System
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

def run_backtest(ticker, name, start="2015-01-01", end="2024-12-31"):
    print(f"\n{'='*70}")
    print(f"  HURST CYCLIC TRADING — {name} ({ticker})")
    print(f"{'='*70}")

    # ── 1. FETCH DATA ──────────────────────────────────────────────────────
    print(f"\n[1] Downloading {name} data...")
    raw = yf.download(ticker, start=start, end=end, auto_adjust=True)
    raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
    data = raw[["Close"]].dropna().copy()
    data.columns = ["Close"]
    data = data[data["Close"] > 0]

    print(f"    Bars loaded : {len(data)}")
    print(f"    Date range  : {data.index[0].date()} → {data.index[-1].date()}")
    print(f"    Price range : ${data['Close'].min():.2f} → ${data['Close'].max():.2f}")

    prices = data["Close"].values

    # ── 2. REGIME DETECTION ────────────────────────────────────────────────
    ema50 = pd.Series(prices).ewm(span=50).mean().values
    regimes, regime_labels = [], []
    for i in range(len(prices)):
        if i < 50:
            regimes.append(0); regime_labels.append("NEUTRAL"); continue
        slope = (ema50[i] - ema50[i-20]) / ema50[i-20] * 100
        pos   = (prices[i] - ema50[i]) / ema50[i] * 100
        vol   = np.std(prices[max(0,i-20):i]) / np.mean(prices[max(0,i-20):i]) * 100
        if   slope > 1.5 and pos > 1.0:  r, l = 2, "STRONG_UPTREND"
        elif slope > 0.5 and pos > 0:    r, l = 1, "UPTREND"
        elif slope < -1.5 and pos < -1:  r, l = -2, "STRONG_DOWNTREND"
        elif slope < -0.5 and pos < 0:   r, l = -1, "DOWNTREND"
        elif vol < 1.5:                  r, l = 0, "RANGING"
        else:                            r, l = 0, "NEUTRAL"
        regimes.append(r); regime_labels.append(l)

    # ── 3. SIGNAL GENERATION ───────────────────────────────────────────────
    def centered_ma(prices, period):
        half = period // 2
        result = np.full(len(prices), np.nan)
        for i in range(half, len(prices) - half):
            result[i] = np.mean(prices[i-half:i+half+1])
        return result

    cma_s = centered_ma(prices, 10)
    cma_l = centered_ma(prices, 20)
    signals = np.zeros(len(prices))
    for i in range(20, len(prices) - 10):
        if np.isnan(cma_s[i]) or np.isnan(cma_l[i]): continue
        if regimes[i] <= 0: continue
        if (prices[i] > cma_s[i] and cma_s[i] > cma_l[i] and prices[i-1] <= cma_s[i-1]):
            signals[i] = 1
        elif (prices[i] < cma_s[i] and prices[i-1] >= cma_s[i-1]):
            signals[i] = -1

    # ── 4. BACKTEST ────────────────────────────────────────────────────────
    capital, position, entry_price = 100_000, 0, 0
    entry_date, trades, equity_curve = None, [], [100_000]

    for i in range(1, len(prices)):
        regime_factor = {2: 1.0, 1: 0.7, 0: 0.3, -1: 0.0, -2: 0.0}.get(regimes[i], 0.3)

        if signals[i] == 1 and position == 0:
            pos_size = (capital * 0.02 * regime_factor) / (prices[i] * 0.02)
            if pos_size > 0:
                position, entry_price, entry_date = pos_size, prices[i], data.index[i]

        elif position > 0:
            stop   = entry_price * 0.97
            target = entry_price * 1.08
            reason = None
            if   prices[i] <= stop:    reason = "STOP_LOSS"
            elif prices[i] >= target:  reason = "TAKE_PROFIT"
            elif signals[i] == -1:     reason = "SIGNAL_EXIT"
            elif regimes[i] < 0:       reason = "REGIME_EXIT"

            if reason:
                pnl = (prices[i] - entry_price) * position
                capital += pnl
                trades.append({
                    "entry_date": entry_date, "exit_date": data.index[i],
                    "entry_price": entry_price, "exit_price": prices[i],
                    "pnl": pnl, "return_pct": (prices[i]/entry_price - 1)*100,
                    "exit_reason": reason, "regime": regime_labels[i]
                })
                position = 0
        equity_curve.append(capital)

    # ── 5. RESULTS ─────────────────────────────────────────────────────────
    trades_df = pd.DataFrame(trades)
    n = len(trades_df)
    if n == 0:
        print("  No trades generated."); return None

    winners = trades_df[trades_df["pnl"] > 0]
    losers  = trades_df[trades_df["pnl"] <= 0]
    hit_rate = len(winners) / n * 100
    avg_win  = winners["pnl"].mean() if len(winners) > 0 else 0
    avg_loss = losers["pnl"].mean()  if len(losers)  > 0 else 0
    total_ret = (capital - 100_000) / 100_000 * 100
    eq = np.array(equity_curve)
    max_dd = ((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq) * 100).min()
    returns = pd.Series(equity_curve).pct_change().dropna()
    sharpe  = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
    pf = abs(winners["pnl"].sum() / losers["pnl"].sum()) if len(losers) > 0 else float("inf")
    expectancy = (hit_rate/100 * avg_win) + ((1 - hit_rate/100) * avg_loss)

    print(f"\n  OVERVIEW")
    print(f"    Starting capital : $100,000")
    print(f"    Final capital    : ${capital:,.2f}")
    print(f"    Total return     : {total_ret:+.2f}%")
    print(f"    Buy & hold return: {(prices[-1]/prices[0]-1)*100:+.2f}%")

    print(f"\n  TRADE STATISTICS")
    print(f"    Total trades     : {n}")
    print(f"    Win rate         : {hit_rate:.1f}%")
    print(f"    Avg win          : ${avg_win:,.2f}")
    print(f"    Avg loss         : ${avg_loss:,.2f}")
    print(f"    Profit factor    : {pf:.2f}x")
    print(f"    Expectancy/trade : ${expectancy:,.2f}")

    print(f"\n  RISK METRICS")
    print(f"    Sharpe ratio     : {sharpe:.2f}")
    print(f"    Max drawdown     : {max_dd:.2f}%")

    print(f"\n  REGIME PERFORMANCE")
    for regime in ["STRONG_UPTREND", "UPTREND", "NEUTRAL", "RANGING"]:
        r_t = trades_df[trades_df["regime"] == regime]
        if len(r_t) == 0: continue
        r_wr  = len(r_t[r_t["pnl"] > 0]) / len(r_t) * 100
        r_pnl = r_t["pnl"].sum()
        print(f"    {regime:<20}: {len(r_t):>3} trades | {r_wr:.0f}% WR | ${r_pnl:,.0f} PnL")

    if   total_ret > 100 and sharpe > 1.0 and hit_rate > 50: verdict = "EXCELLENT ✅ — Add to scanner"
    elif total_ret > 50  and sharpe > 0.8:                   verdict = "STRONG ✅ — Good candidate"
    elif total_ret > 20  and sharpe > 0.5:                   verdict = "MODERATE ⚠️ — Usable"
    elif total_ret > 0:                                       verdict = "WEAK ⚠️ — Marginally positive"
    else:                                                     verdict = "POOR ❌ — Avoid"

    print(f"\n  VERDICT: {verdict}")

    trades_df.to_csv(f"{ticker.lower()}_backtest_trades.csv", index=False)
    print(f"  Saved: {ticker.lower()}_backtest_trades.csv")
    return {"ticker": ticker, "return": total_ret, "sharpe": sharpe,
            "hit_rate": hit_rate, "final_capital": capital, "verdict": verdict}

# ── RUN BOTH BACKTESTS ────────────────────────────────────────────────────────
results = []
results.append(run_backtest("NVDA", "Nvidia"))
results.append(run_backtest("SOXX", "Semiconductor ETF"))

# ── COMPARISON SUMMARY ────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("  COMPARISON SUMMARY")
print(f"{'='*70}")
print(f"  {'Asset':<25} {'Return':>10} {'Sharpe':>8} {'Win Rate':>10} {'Verdict'}")
print(f"  {'-'*65}")
for r in results:
    if r:
        print(f"  {r['ticker']:<25} {r['return']:>+9.1f}% {r['sharpe']:>8.2f} {r['hit_rate']:>9.1f}% {r['verdict']}")
print(f"{'='*70}")
