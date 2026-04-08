"""
Universe Market Scanner
Scans 100+ markets and identifies strong uptrend opportunities
using Hurst cyclic regime detection and momentum scoring
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
from datetime import datetime
warnings.filterwarnings("ignore")

# ── MARKET UNIVERSE ───────────────────────────────────────────────────────────
UNIVERSE = {
    "CRYPTO": [
        "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
        "ADA-USD", "AVAX-USD", "DOT-USD", "LINK-USD", "MATIC-USD"
    ],
    "MEGA_CAP_TECH": [
        "NVDA", "AAPL", "MSFT", "GOOGL", "META",
        "AMZN", "TSLA", "AMD", "AVGO", "ORCL"
    ],
    "SEMICONDUCTORS": [
        "SOXX", "SMH", "QCOM", "MU", "AMAT",
        "LRCX", "KLAC", "TXN", "MRVL", "ON"
    ],
    "GROWTH_ETFs": [
        "QQQ", "IWF", "VUG", "ARKK", "TQQQ",
        "IGV", "CIBR", "CLOU", "BOTZ", "ROBO"
    ],
    "SECTOR_ETFs": [
        "XLK", "XLF", "XLE", "XLV", "XLI",
        "XLY", "XLRE", "XLC", "XLB", "XLU"
    ],
    "COMMODITIES": [
        "GLD", "SLV", "PDBC", "DBA", "CPER",
        "URA", "REMX", "PICK", "XME", "FCX"
    ],
    "INTERNATIONAL": [
        "EWJ", "FXI", "EWZ", "EWY", "INDA",
        "VWO", "EEM", "EWG", "EWU", "MCHI"
    ],
    "HIGH_MOMENTUM": [
        "MSFT", "NFLX", "CRM", "NOW", "SNOW",
        "PLTR", "COIN", "MSTR", "SMCI", "HOOD"
    ]
}

def analyze_market(ticker):
    """Full Hurst regime + momentum analysis for a single ticker."""
    try:
        raw = yf.download(ticker, period="1y", interval="1d",
                         auto_adjust=True, progress=False)
        if raw.empty or len(raw) < 60:
            return None

        raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
        prices = raw["Close"].values.flatten()
        volume = raw["Volume"].values.flatten() if "Volume" in raw.columns else None

        # ── REGIME DETECTION ──────────────────────────────────────────────
        ema20  = pd.Series(prices).ewm(span=20).mean().values
        ema50  = pd.Series(prices).ewm(span=50).mean().values
        ema200 = pd.Series(prices).ewm(span=200).mean().values

        slope_20  = (ema20[-1]  - ema20[-10])  / ema20[-10]  * 100
        slope_50  = (ema50[-1]  - ema50[-20])  / ema50[-20]  * 100
        slope_200 = (ema200[-1] - ema200[-50]) / ema200[-50] * 100

        pos_vs_20  = (prices[-1] - ema20[-1])  / ema20[-1]  * 100
        pos_vs_50  = (prices[-1] - ema50[-1])  / ema50[-1]  * 100
        pos_vs_200 = (prices[-1] - ema200[-1]) / ema200[-1] * 100

        # ── MOMENTUM METRICS ──────────────────────────────────────────────
        ret_1w  = (prices[-1] / prices[-5]  - 1) * 100  if len(prices) > 5  else 0
        ret_1m  = (prices[-1] / prices[-21] - 1) * 100  if len(prices) > 21 else 0
        ret_3m  = (prices[-1] / prices[-63] - 1) * 100  if len(prices) > 63 else 0
        ret_6m  = (prices[-1] / prices[-126]- 1) * 100  if len(prices) > 126 else 0

        # ── VOLATILITY ────────────────────────────────────────────────────
        returns = pd.Series(prices).pct_change().dropna()
        vol_20  = returns[-20:].std() * np.sqrt(252) * 100

        # ── RSI ───────────────────────────────────────────────────────────
        delta  = pd.Series(prices).diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = gain / loss
        rsi    = (100 - (100 / (1 + rs))).iloc[-1]

        # ── REGIME CLASSIFICATION ─────────────────────────────────────────
        ema_aligned  = ema20[-1] > ema50[-1] > ema200[-1]  # all EMAs stacked bullishly
        price_above  = prices[-1] > ema20[-1] and prices[-1] > ema50[-1]
        strong_slope = slope_50 > 1.0 and slope_200 > 0.3

        if ema_aligned and price_above and strong_slope and slope_20 > 0.5:
            regime = "STRONG_UPTREND"
            regime_score = 1.0
        elif (ema20[-1] > ema50[-1] and prices[-1] > ema50[-1] and slope_50 > 0.3):
            regime = "UPTREND"
            regime_score = 0.7
        elif slope_50 < -1.0 and prices[-1] < ema50[-1]:
            regime = "STRONG_DOWNTREND"
            regime_score = 0.0
        elif slope_50 < -0.3 or prices[-1] < ema50[-1]:
            regime = "DOWNTREND"
            regime_score = 0.1
        else:
            regime = "NEUTRAL"
            regime_score = 0.3

        # ── COMPOSITE SCORE ───────────────────────────────────────────────
        trend_score    = min(max(slope_50 / 5.0, 0), 1.0)
        momentum_score = min(max(ret_3m / 50.0, 0), 1.0)
        rsi_score      = 1.0 if 55 < rsi < 75 else (0.5 if 45 < rsi <= 55 else 0.2)
        align_score    = 1.0 if ema_aligned else 0.3

        quality = (
            regime_score   * 0.35 +
            trend_score    * 0.25 +
            momentum_score * 0.20 +
            rsi_score      * 0.10 +
            align_score    * 0.10
        )

        return {
            "ticker":       ticker,
            "regime":       regime,
            "quality":      round(quality, 4),
            "price":        round(float(prices[-1]), 2),
            "ret_1w":       round(ret_1w, 2),
            "ret_1m":       round(ret_1m, 2),
            "ret_3m":       round(ret_3m, 2),
            "ret_6m":       round(ret_6m, 2),
            "slope_50":     round(slope_50, 3),
            "pos_vs_50":    round(pos_vs_50, 2),
            "rsi":          round(float(rsi), 1),
            "vol_20":       round(vol_20, 1),
            "ema_aligned":  ema_aligned,
        }

    except Exception as e:
        return None


def run_universe_scan(min_regime="UPTREND", top_n=15):
    """Scan entire universe and return top trending markets."""

    print("=" * 70)
    print("  UNIVERSE MARKET SCANNER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_tickers = []
    for sector, tickers in UNIVERSE.items():
        all_tickers.extend([(t, sector) for t in tickers])

    total = len(all_tickers)
    results = []
    scanned = 0

    for ticker, sector in all_tickers:
        scanned += 1
        print(f"\r  Scanning {scanned}/{total}: {ticker:<12}", end="", flush=True)
        result = analyze_market(ticker)
        if result:
            result["sector"] = sector
            results.append(result)

    print(f"\r  Scanned {scanned}/{total} markets complete!{' '*20}")

    if not results:
        print("  No results returned.")
        return

    df = pd.DataFrame(results)

    # ── FILTER: UPTREND or STRONG_UPTREND only ────────────────────────────
    trending = df[df["regime"].isin(["STRONG_UPTREND", "UPTREND"])].copy()
    trending = trending.sort_values("quality", ascending=False)

    # ── FULL SUMMARY ──────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  SCAN SUMMARY")
    print(f"{'='*70}")
    print(f"  Total scanned      : {len(df)}")
    for regime in ["STRONG_UPTREND", "UPTREND", "NEUTRAL", "DOWNTREND", "STRONG_DOWNTREND"]:
        count = len(df[df["regime"] == regime])
        bar   = "█" * count
        print(f"  {regime:<22}: {count:>3}  {bar}")

    # ── TOP STRONG UPTRENDS ───────────────────────────────────────────────
    strong = df[df["regime"] == "STRONG_UPTREND"].sort_values("quality", ascending=False)
    if len(strong) > 0:
        print(f"\n{'='*70}")
        print(f"  ⭐ STRONG UPTREND OPPORTUNITIES ({len(strong)} found)")
        print(f"{'='*70}")
        print(f"  {'Ticker':<10} {'Sector':<20} {'Score':>6} {'1W%':>6} {'1M%':>6} {'3M%':>7} {'RSI':>5} {'EMA✓'}")
        print(f"  {'-'*68}")
        for _, row in strong.head(top_n).iterrows():
            ema_check = "✓" if row["ema_aligned"] else " "
            print(f"  {row['ticker']:<10} {row['sector']:<20} {row['quality']:>6.3f} "
                  f"{row['ret_1w']:>+6.1f}% {row['ret_1m']:>+6.1f}% "
                  f"{row['ret_3m']:>+7.1f}% {row['rsi']:>5.0f} {ema_check:>5}")
    else:
        print(f"\n  ⚠️  No STRONG_UPTREND markets found — market may be broadly bearish")

    # ── ALL TRENDING (UPTREND + STRONG) ───────────────────────────────────
    if len(trending) > 0:
        print(f"\n{'='*70}")
        print(f"  📈 ALL TRENDING MARKETS (Top {min(top_n, len(trending))})")
        print(f"{'='*70}")
        print(f"  {'Ticker':<10} {'Regime':<22} {'Score':>6} {'3M%':>7} {'RSI':>5}")
        print(f"  {'-'*55}")
        for _, row in trending.head(top_n).iterrows():
            print(f"  {row['ticker']:<10} {row['regime']:<22} {row['quality']:>6.3f} "
                  f"{row['ret_3m']:>+7.1f}% {row['rsi']:>5.0f}")

    # ── ADD TO TRADING SYSTEM RECOMMENDATION ──────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RECOMMENDATION")
    print(f"{'='*70}")
    if len(strong) >= 3:
        top3 = strong.head(3)["ticker"].tolist()
        print(f"  Add these to your scanner: {', '.join(top3)}")
        print(f"  These show the strongest Hurst cyclic uptrend conditions")
    elif len(trending) > 0:
        top3 = trending.head(3)["ticker"].tolist()
        print(f"  Moderate opportunities: {', '.join(top3)}")
        print(f"  Market is not strongly bullish — reduce position sizes")
    else:
        print(f"  ⚠️  STAY IN CASH — No quality uptrends detected across universe")
        print(f"  Wait for market conditions to improve")

    # ── SAVE RESULTS ──────────────────────────────────────────────────────
    df.to_csv("universe_scan_results.csv", index=False)
    print(f"\n  Full results saved to: universe_scan_results.csv")
    print(f"{'='*70}\n")

    return trending


if __name__ == "__main__":
    run_universe_scan()
