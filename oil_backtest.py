"""
Oil (WTI Crude) Backtest using Hurst Cyclic Trading System
Ticker: CL=F (WTI Crude Oil Futures via yfinance)
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

# ── 1. FETCH OIL DATA ──────────────────────────────────────────────────────────
print("=" * 70)
print("  HURST CYCLIC TRADING — OIL (WTI CRUDE) BACKTEST")
print("=" * 70)

print("\n[1] Downloading Oil data (USO - US Oil Fund ETF)...")
raw = yf.download("USO", start="2015-01-01", end="2024-12-31", auto_adjust=True)
raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
data = raw[["Close"]].dropna().copy()
data.columns = ["Close"]
data = data[data["Close"] > 0]

print(f"    Bars loaded : {len(data)}")
print(f"    Date range  : {data.index[0].date()} → {data.index[-1].date()}")
print(f"    Price range : ${data['Close'].min():.2f} → ${data['Close'].max():.2f}")

# ── 2. REGIME DETECTION ───────────────────────────────────────────────────────
print("\n[2] Running regime detection...")
prices = data["Close"].values
ema50  = pd.Series(prices).ewm(span=50).mean().values

regimes, regime_labels = [], []
for i in range(len(prices)):
    if i < 50:
        regimes.append(0); regime_labels.append("NEUTRAL")
        continue
    slope   = (ema50[i] - ema50[i-20]) / ema50[i-20] * 100
    pos     = (prices[i] - ema50[i]) / ema50[i] * 100
    vol     = np.std(prices[max(0,i-20):i]) / np.mean(prices[max(0,i-20):i]) * 100

    if slope > 1.5 and pos > 1.0:
        r, l = 2, "STRONG_UPTREND"
    elif slope > 0.5 and pos > 0:
        r, l = 1, "UPTREND"
    elif slope < -1.5 and pos < -1.0:
        r, l = -2, "STRONG_DOWNTREND"
    elif slope < -0.5 and pos < 0:
        r, l = -1, "DOWNTREND"
    elif vol < 1.5:
        r, l = 0, "RANGING"
    else:
        r, l = 0, "NEUTRAL"
    regimes.append(r); regime_labels.append(l)

data["Regime"]      = regimes
data["RegimeLabel"] = regime_labels

regime_counts = pd.Series(regime_labels).value_counts()
print(f"    Regime distribution:")
for regime, count in regime_counts.items():
    pct = count / len(regime_labels) * 100
    print(f"      {regime:<20}: {count:>4} bars ({pct:.1f}%)")

# ── 3. HURST SIGNAL GENERATION ───────────────────────────────────────────────
print("\n[3] Generating Hurst cyclic signals...")

def centered_ma(prices, period):
    half = period // 2
    result = np.full(len(prices), np.nan)
    for i in range(half, len(prices) - half):
        result[i] = np.mean(prices[i-half:i+half+1])
    return result

def generate_signals(prices, regimes, short=10, long=20, trend_required=True):
    cma_short = centered_ma(prices, short)
    cma_long  = centered_ma(prices, long)
    signals   = np.zeros(len(prices))

    for i in range(long, len(prices) - long//2):
        if np.isnan(cma_short[i]) or np.isnan(cma_long[i]):
            continue
        if trend_required and regimes[i] <= 0:
            continue

        # Buy: price crosses above short CMA and short > long (uptrend)
        if (prices[i] > cma_short[i] and
            cma_short[i] > cma_long[i] and
            prices[i-1] <= cma_short[i-1]):
            signals[i] = 1

        # Sell: price crosses below short CMA
        elif (prices[i] < cma_short[i] and
              prices[i-1] >= cma_short[i-1]):
            signals[i] = -1

    return signals, cma_short, cma_long

signals, cma_s, cma_l = generate_signals(prices, regimes)
buy_signals  = np.sum(signals ==  1)
sell_signals = np.sum(signals == -1)
print(f"    Buy signals  : {buy_signals}")
print(f"    Sell signals : {sell_signals}")

# ── 4. BACKTEST ENGINE ────────────────────────────────────────────────────────
print("\n[4] Running backtest...")

capital       = 100_000
position      = 0
entry_price   = 0
trades        = []
equity_curve  = [capital]
risk_per_trade = 0.02

for i in range(1, len(prices)):
    regime_factor = {2: 1.0, 1: 0.7, 0: 0.3, -1: 0.0, -2: 0.0}.get(regimes[i], 0.3)

    # Entry
    if signals[i] == 1 and position == 0:
        position_size = (capital * risk_per_trade * regime_factor) / (prices[i] * 0.02)
        if position_size > 0:
            position    = position_size
            entry_price = prices[i]
            entry_date  = data.index[i]

    # Exit
    elif position > 0:
        stop_loss   = entry_price * 0.97
        take_profit = entry_price * 1.06

        exit_reason = None
        exit_price  = prices[i]

        if prices[i] <= stop_loss:
            exit_reason = "STOP_LOSS"
        elif prices[i] >= take_profit:
            exit_reason = "TAKE_PROFIT"
        elif signals[i] == -1:
            exit_reason = "SIGNAL_EXIT"
        elif regimes[i] < 0:
            exit_reason = "REGIME_EXIT"

        if exit_reason:
            pnl     = (exit_price - entry_price) * position
            capital += pnl
            trades.append({
                "entry_date"  : entry_date,
                "exit_date"   : data.index[i],
                "entry_price" : entry_price,
                "exit_price"  : exit_price,
                "pnl"         : pnl,
                "return_pct"  : (exit_price / entry_price - 1) * 100,
                "exit_reason" : exit_reason,
                "regime"      : regime_labels[i]
            })
            position = 0

    equity_curve.append(capital)

# ── 5. RESULTS ────────────────────────────────────────────────────────────────
print("\n[5] Calculating performance metrics...")

trades_df  = pd.DataFrame(trades)
n_trades   = len(trades_df)

if n_trades > 0:
    winners    = trades_df[trades_df["pnl"] > 0]
    losers     = trades_df[trades_df["pnl"] <= 0]
    hit_rate   = len(winners) / n_trades * 100
    avg_win    = winners["pnl"].mean()    if len(winners) > 0 else 0
    avg_loss   = losers["pnl"].mean()     if len(losers)  > 0 else 0
    total_pnl  = trades_df["pnl"].sum()
    total_ret  = (capital - 100_000) / 100_000 * 100

    eq         = np.array(equity_curve)
    roll_max   = np.maximum.accumulate(eq)
    drawdowns  = (eq - roll_max) / roll_max * 100
    max_dd     = drawdowns.min()

    returns    = pd.Series(equity_curve).pct_change().dropna()
    sharpe     = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
    profit_factor = abs(winners["pnl"].sum() / losers["pnl"].sum()) if len(losers) > 0 else float("inf")
    expectancy = (hit_rate/100 * avg_win) + ((1 - hit_rate/100) * avg_loss)

    print("\n" + "=" * 70)
    print("  OIL (WTI CRUDE) BACKTEST RESULTS")
    print("=" * 70)
    print(f"\n  OVERVIEW")
    print(f"    Asset           : WTI Crude Oil (USO ETF)")
    print(f"    Period          : 2015 – 2024 (10 years)")
    print(f"    Starting capital: $100,000")
    print(f"    Final capital   : ${capital:,.2f}")
    print(f"    Total return    : {total_ret:+.2f}%")

    print(f"\n  TRADE STATISTICS")
    print(f"    Total trades    : {n_trades}")
    print(f"    Winners         : {len(winners)} ({hit_rate:.1f}%)")
    print(f"    Losers          : {len(losers)} ({100-hit_rate:.1f}%)")
    print(f"    Avg win         : ${avg_win:,.2f}")
    print(f"    Avg loss        : ${avg_loss:,.2f}")
    print(f"    Profit factor   : {profit_factor:.2f}x")
    print(f"    Expectancy/trade: ${expectancy:,.2f}")

    print(f"\n  RISK METRICS")
    print(f"    Sharpe ratio    : {sharpe:.2f}")
    print(f"    Max drawdown    : {max_dd:.2f}%")

    print(f"\n  EXIT BREAKDOWN")
    for reason, count in trades_df["exit_reason"].value_counts().items():
        pct = count / n_trades * 100
        avg = trades_df[trades_df["exit_reason"] == reason]["pnl"].mean()
        print(f"    {reason:<20}: {count:>3} trades ({pct:.0f}%) | avg PnL ${avg:,.0f}")

    print(f"\n  REGIME PERFORMANCE")
    for regime in trades_df["regime"].unique():
        r_trades = trades_df[trades_df["regime"] == regime]
        r_wins   = len(r_trades[r_trades["pnl"] > 0])
        r_wr     = r_wins / len(r_trades) * 100
        r_pnl    = r_trades["pnl"].sum()
        print(f"    {regime:<20}: {len(r_trades):>3} trades | {r_wr:.0f}% WR | ${r_pnl:,.0f} PnL")

    # Verdict
    print("\n" + "=" * 70)
    print("  VERDICT")
    if total_ret > 20 and sharpe > 0.8 and hit_rate > 50:
        verdict = "STRONG — Oil shows good Hurst cyclic behaviour"
    elif total_ret > 0 and sharpe > 0.5:
        verdict = "MODERATE — Oil is tradeable but less ideal than crypto/equities"
    elif total_ret > 0:
        verdict = "WEAK — Positive but low risk-adjusted returns on oil"
    else:
        verdict = "POOR — Hurst system underperforms on oil in this period"
    print(f"  {verdict}")
    print("=" * 70)

    trades_df.to_csv("oil_backtest_trades.csv", index=False)
    print(f"\n  Trades saved to: oil_backtest_trades.csv")

else:
    print("  No trades generated — check signal parameters.")
