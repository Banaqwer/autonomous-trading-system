"""
PHASE 1A-MT SIMPLE TEST
============================================================================

Simplified test of multi-timeframe approach
- Tests data availability and algorithm functionality
- Focuses on understanding why algorithms are failing
- Uses longer date ranges to ensure cycle detection

Run: python phase1a_mt_simple_test.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm

print("\n" + "="*80)
print("PHASE 1A-MT SIMPLE TEST - Understanding Multi-Timeframe")
print("="*80)

# Test with longer history to ensure cycles detected
print("\n[TEST 1] Daily + 4H on IWM (2 years window)")
print("-"*80)

print("Downloading data...")
daily_data = yf.download('IWM', start='2023-01-01', end='2025-01-01', progress=False)
print(f"Daily data: {len(daily_data)} bars from {daily_data.index[0].date()} to {daily_data.index[-1].date()}")

print("Running daily Hurst...")
old_stdout = sys.stdout
sys.stdout = StringIO()

try:
    algo_daily = HurstCyclicAlgorithm(daily_data, use_fld=True)
    report_daily = algo_daily.run()

    sys.stdout = old_stdout

    if algo_daily.report is not None:
        trades = algo_daily.report.get('total_trades', 0)
        wr = algo_daily.report.get('win_rate', 0)
        ret = algo_daily.report.get('total_return_pct', 0)
        print(f"Result: {trades} trades, {wr:.0%} WR, {ret:.2f}% return")
    else:
        print("Result: No report generated")
except Exception as e:
    sys.stdout = old_stdout
    print(f"Error: {str(e)[:80]}")

# Try 4H data
print("\nDownloading 4H data...")
# Use shorter range for 4H since yfinance limits to 730 days (use last year only)
try:
    h4_data = yf.download('IWM', start='2025-04-06', end='2026-04-06', interval='4h', progress=False)
    if h4_data is None or h4_data.empty:
        print("No 4H data available")
        h4_data = None
    else:
        print(f"4H data: {len(h4_data)} bars from {h4_data.index[0]} to {h4_data.index[-1]}")
except Exception as e:
    print(f"Error downloading 4H data: {str(e)[:50]}")
    h4_data = None

if h4_data is not None:
    print("Running 4H Hurst...")
    sys.stdout = StringIO()

    try:
        algo_h4 = HurstCyclicAlgorithm(h4_data, use_fld=True)
        report_h4 = algo_h4.run()

        sys.stdout = old_stdout

        if algo_h4.report is not None:
            trades = algo_h4.report.get('total_trades', 0)
            wr = algo_h4.report.get('win_rate', 0)
            ret = algo_h4.report.get('total_return_pct', 0)
            print(f"Result: {trades} trades, {wr:.0%} WR, {ret:.2f}% return")
        else:
            print("Result: No report generated")
    except Exception as e:
        sys.stdout = old_stdout
        print(f"Error: {str(e)[:80]}")
else:
    algo_h4 = None
    print("Result: No 4H data available")

# Test combined frequency
print("\n" + "="*80)
print("ANALYSIS")
print("="*80)

if algo_daily.report and algo_h4 and algo_h4.report:
    daily_trades = algo_daily.report.get('total_trades', 0)
    h4_trades = algo_h4.report.get('total_trades', 0)
    daily_wr = algo_daily.report.get('win_rate', 0)
    h4_wr = algo_h4.report.get('win_rate', 0)

    total = daily_trades + h4_trades
    coordinated = total * 0.8
    blended_wr = (daily_wr + h4_wr) / 2

    print(f"\nDaily:      {daily_trades} trades @ {daily_wr:.0%}")
    print(f"4H:         {h4_trades} trades @ {h4_wr:.0%}")
    print(f"Combined:   {total} trades ({coordinated:.0f} coordinated)")
    print(f"Blended WR: {blended_wr:.0%}")

    # Check if operational
    if coordinated >= 15 and blended_wr >= 0.65:
        print(f"\n[OPERATIONAL] ✓ Meets criteria (15+ signals, 65%+ WR)")
    else:
        print(f"\n[NOT OPERATIONAL]")
        if coordinated < 15:
            print(f"  Need +{15-coordinated:.0f} signals/year")
        if blended_wr < 0.65:
            print(f"  Need +{(0.65-blended_wr)*100:.0f}% win rate improvement")

print("\n" + "="*80)
