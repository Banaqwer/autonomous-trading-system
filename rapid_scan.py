"""
RAPID ASSET SCAN
============================================================================

Quick scan through assets to find high-frequency, high-edge assets
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO
from datetime import datetime

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


def test_asset(symbol, start_date='2023-01-01', end_date='2024-12-31'):
    """Test single asset and return results or None"""
    try:
        print(f"  Testing {symbol}...", end=' ', flush=True)

        # Download data
        data = yf.download(symbol, start=start_date, end=end_date, progress=False)

        if data.empty or len(data) < 50:
            print("SKIP (no data)")
            return None

        # Run algorithm (suppress output)
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        algo = HurstCyclicAlgorithm(data, use_fld=True)
        report = algo.run()

        sys.stdout = old_stdout

        # Check if we have a report
        if not hasattr(algo, 'report'):
            print("SKIP (no report)")
            return None

        num_trades = algo.report.get('total_trades', 0)
        if num_trades == 0:
            print("SKIP (no trades)")
            return None

        win_rate = algo.report.get('win_rate', 0)
        if win_rate < 0.50:
            print(f"SKIP (WR={win_rate:.1%})")
            return None

        # Calculate trades per year
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        years = days / 365.25
        trades_per_year = num_trades / years

        # Return data
        total_return = algo.report.get('total_return_pct', 0) / 100
        max_dd = algo.report.get('max_drawdown', 0)
        sharpe = algo.report.get('sharpe_ratio', 0)

        freq_status = "HI-FREQ" if trades_per_year >= 15 else "LO-FREQ"
        edge_status = "EDGE" if win_rate >= 0.65 else "WEAK"

        print(f"OK [{freq_status} {edge_status}] {num_trades:2} trades ({trades_per_year:5.1f}/yr) WR={win_rate:.0%}")

        return {
            'symbol': symbol,
            'num_trades': num_trades,
            'trades_per_year': trades_per_year,
            'win_rate': win_rate,
            'total_return': total_return,
            'max_drawdown': max_dd,
            'sharpe_ratio': sharpe,
        }

    except Exception as e:
        sys.stdout = old_stdout
        print(f"ERROR ({str(e)[:30]})")
        return None


# Define asset categories
print("="*80)
print("RAPID ASSET SCAN - Finding High-Frequency, High-Edge Assets")
print("="*80)
print()

assets_to_test = {
    'Equity Indices': ['SPY', 'QQQ', 'IWM', 'EEM'],
    'Sector ETFs': ['XLK', 'XLE', 'XLV'],
    'Commodity/FX': ['GLD', 'SLV', 'USO', 'DBC'],
    'Bonds': ['TLT', 'HYG'],
    'Intl': ['EWJ', 'FXI'],
    'Other': ['VNQ', 'VXX'],
}

results = []

for category, symbols in assets_to_test.items():
    print(f"\n[{category}]")
    for symbol in symbols:
        result = test_asset(symbol)
        if result:
            results.append(result)

# Generate report
print("\n" + "="*80)
print("SCAN COMPLETE")
print("="*80)

if results:
    df = pd.DataFrame(results)

    # Find high-frequency, high-edge assets
    optimal = df[(df['trades_per_year'] >= 15) & (df['win_rate'] >= 0.65)].copy()

    print(f"\nTotal assets tested:              {len(assets_to_test['Equity Indices']) + len(assets_to_test['Sector ETFs']) + len(assets_to_test['Commodity/FX']) + len(assets_to_test['Bonds']) + len(assets_to_test['Intl']) + len(assets_to_test['Other'])}")
    print(f"Assets with valid signals:        {len(df)}")
    print(f"High-frequency (15+/yr):          {len(df[df['trades_per_year'] >= 15])}")
    print(f"High-edge (65%+ WR):              {len(df[df['win_rate'] >= 0.65])}")
    print(f"OPTIMAL (both criteria):          {len(optimal)}")

    if len(optimal) > 0:
        print(f"\n[OPTIMAL ASSETS FOR PHASE 1A]")
        print(f"Recommended for deployment:")
        optimal_sorted = optimal.sort_values('trades_per_year', ascending=False)
        for idx, row in optimal_sorted.iterrows():
            print(f"  {row['symbol']:8} | {row['trades_per_year']:5.1f} trades/yr | WR={row['win_rate']:.1%} | Return={row['total_return']:.2%}")

        avg_daily = optimal['trades_per_year'].mean() / 252
        print(f"\nAverage daily signal generation: {avg_daily:.2f} signals/day")
    else:
        print(f"\n[NO OPTIMAL ASSETS FOUND]")
        print(f"Assets need either higher frequency OR higher edge")
        print(f"\nBest candidates by frequency:")
        for idx, row in df.sort_values('trades_per_year', ascending=False).head(5).iterrows():
            freq_gap = 15 - row['trades_per_year']
            print(f"  {row['symbol']:8} | {row['trades_per_year']:5.1f} trades/yr (need +{freq_gap:.1f}) | WR={row['win_rate']:.1%}")

        print(f"\nBest candidates by edge:")
        for idx, row in df.sort_values('win_rate', ascending=False).head(5).iterrows():
            wr_gap = 0.65 - row['win_rate']
            print(f"  {row['symbol']:8} | WR={row['win_rate']:.1%} (need +{wr_gap:.1%}) | {row['trades_per_year']:5.1f} trades/yr")
else:
    print("\nNo valid assets found in scan")
