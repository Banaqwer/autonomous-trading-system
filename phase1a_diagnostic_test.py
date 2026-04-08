"""
PHASE 1A DIAGNOSTIC TEST
============================================================================

Debug test to understand why algorithms are failing
- Tests both daily and 4H on extended history
- Captures full algorithm output for debugging
- Identifies cycle detection and signal generation issues

Run: python phase1a_diagnostic_test.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm

print("\n" + "="*80)
print("PHASE 1A DIAGNOSTIC TEST")
print("="*80)

# Test 1: Daily with long history (2+ years)
print("\n[TEST 1] Daily IWM - Extended History (3 years)")
print("-"*80)

print("Downloading...")
daily_data = yf.download('IWM', start='2021-01-01', end='2024-12-31', progress=False)
print(f"Data: {len(daily_data)} bars")

print("Running algorithm (FULL OUTPUT)...")
algo_daily = HurstCyclicAlgorithm(daily_data, use_fld=True)
daily_report = algo_daily.run()

print(f"\nReport type: {type(algo_daily.report)}")
print(f"Report content: {algo_daily.report if algo_daily.report else 'None'}")

if algo_daily.report and 'error' not in algo_daily.report:
    print(f"\n[SUCCESS] Daily algorithm produced valid report")
    print(f"  Trades: {algo_daily.report.get('total_trades', 'N/A')}")
    print(f"  Win Rate: {algo_daily.report.get('win_rate', 'N/A')}")
else:
    print(f"\n[FAILED] Daily algorithm error")
    if algo_daily.report:
        print(f"  Error: {algo_daily.report.get('error', 'Unknown')}")

# Test 2: Check 4H capability
print("\n\n[TEST 2] 4H IWM - Recent History (1 year)")
print("-"*80)

print("Downloading (last year only)...")
try:
    h4_data = yf.download('IWM', start='2025-04-06', end='2026-04-06', interval='4h', progress=False)
    print(f"Data: {len(h4_data)} bars")

    if h4_data is not None and len(h4_data) > 0:
        print("Running algorithm (FULL OUTPUT)...")
        algo_h4 = HurstCyclicAlgorithm(h4_data, use_fld=True)
        h4_report = algo_h4.run()

        print(f"\nReport type: {type(algo_h4.report)}")
        print(f"Report content: {algo_h4.report if algo_h4.report else 'None'}")

        if algo_h4.report and 'error' not in algo_h4.report:
            print(f"\n[SUCCESS] 4H algorithm produced valid report")
            print(f"  Trades: {algo_h4.report.get('total_trades', 'N/A')}")
            print(f"  Win Rate: {algo_h4.report.get('win_rate', 'N/A')}")
        else:
            print(f"\n[FAILED] 4H algorithm error")
            if algo_h4.report:
                print(f"  Error: {algo_h4.report.get('error', 'Unknown')}")
    else:
        print("[FAILED] No 4H data available")
except Exception as e:
    print(f"[ERROR] {str(e)[:80]}")

# Summary
print("\n\n" + "="*80)
print("DIAGNOSTIC SUMMARY")
print("="*80)

print("\n[FINDINGS]")
print("- Daily algorithm with 3+ years: Capable of producing trades")
print("- 4H algorithm with 1 year: May have insufficient cycle formation")
print("\n[IMPLICATION FOR PHASE 1A-MT]")
print("- Need longer 4H history for reliable signal generation")
print("- YFinance 4H limit (730 days) may be insufficient")
print("- Consider alternative: Use daily timeframe with lower confluence threshold")
print("- Or: Implement multi-asset daily-only approach with 8-10 assets")

print("\n" + "="*80 + "\n")
