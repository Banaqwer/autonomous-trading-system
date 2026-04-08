"""
HURST BACKTEST ON VOLATILE MARKET PERIOD
=========================================

Test on 2020-2022: COVID crash + recovery + inflation = highly volatile
This period is MUCH better suited to Hurst cycle strategies.

Run: python backtest_volatile_period.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from hurst_cyclic_trading import HurstCyclicAlgorithm


def backtest_symbol(symbol, start_date, end_date):
    """Run backtest on specific date range."""
    print("\n[{}] Downloading {}...".format(symbol, symbol))

    try:
        # Download data
        df = yf.download(symbol, start=start_date, end=end_date, progress=False)[['Close']]

        if len(df) < 100:
            print("  SKIP: Not enough data ({} bars)".format(len(df)))
            return None

        print("  OK: {} bars from {} to {}".format(
            len(df),
            df.index[0].strftime('%Y-%m-%d'),
            df.index[-1].strftime('%Y-%m-%d')
        ))

        # Run algorithm
        print("  Running algorithm...")
        algo = HurstCyclicAlgorithm(df, use_fld=True)
        report = algo.run()

        if "error" in report:
            print("  ERROR: {}".format(report['error']))
            return None

        # Format results
        result = {
            'symbol': symbol,
            'bars': len(df),
            'trades': report.get('total_trades', 0),
            'win_rate': report.get('win_rate', 0),
            'return': report.get('total_return_pct', 0),
            'max_dd': report.get('max_drawdown', 0),
            'sharpe': report.get('sharpe_ratio', 0),
            'expectancy': report.get('expectancy_per_trade', 0),
        }

        return result

    except Exception as e:
        print("  ERROR: {}".format(str(e)[:100]))
        return None


def main():
    """Main function."""
    print("="*80)
    print("HURST CYCLIC TRADING - VOLATILE MARKET PERIOD TEST")
    print("="*80)
    print("Period: 2020-2022 (COVID crash + recovery + inflation)")
    print("This period has MUCH higher volatility = better for cycles")
    print("="*80)

    # Test on 2020-2022 (highly volatile)
    symbols = ['SPY', 'QQQ', 'GLD', 'TLT', 'UVXY']  # UVXY = volatility index
    start_date = '2020-01-01'
    end_date = '2022-12-31'

    print("\nTesting Period: {} to {}".format(start_date, end_date))
    print("Expected: Higher volatility = more cycle signals = better edge")

    results = []
    for symbol in symbols:
        result = backtest_symbol(symbol, start_date, end_date)
        if result:
            results.append(result)

    # Print summary
    if not results:
        print("\n[!] No successful backtests")
        return

    print("\n" + "="*80)
    print("RESULTS (2020-2022 VOLATILE PERIOD)")
    print("="*80)
    print("Symbol | Trades | Win Rate | Return | Max DD | Sharpe | Exp/Trade")
    print("-"*80)

    for r in results:
        print("{:<8}| {:>6.0f} | {:>7.1%} | {:>6.2f}% | {:>6.2f}% | {:>6.2f} | ${:>7.2f}".format(
            r['symbol'],
            r['trades'],
            r['win_rate'],
            r['return'],
            r['max_dd'],
            r['sharpe'],
            r['expectancy']
        ))

    # Analysis
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)

    valid_trades = [r for r in results if r['trades'] > 0]
    if valid_trades:
        avg_wr = np.mean([r['win_rate'] for r in valid_trades])
        avg_return = np.mean([r['return'] for r in valid_trades])
        avg_sharpe = np.mean([r['sharpe'] for r in valid_trades])

        print("\nAverage (where trades occurred):")
        print("  Win Rate: {:.1%}".format(avg_wr))
        print("  Return: {:.2f}%".format(avg_return))
        print("  Sharpe: {:.2f}".format(avg_sharpe))

        print("\nCycles Generated:")
        total_trades = sum(r['trades'] for r in results)
        assets_with_signals = len(valid_trades)
        print("  Assets with signals: {}/{}".format(assets_with_signals, len(results)))
        print("  Total trades: {}".format(total_trades))

        if avg_wr > 0.50:
            print("\n[+] GOOD: Win rate > 50%")
        if avg_sharpe > 1.0:
            print("[+] GOOD: Sharpe > 1.0")
        if avg_return > 10:
            print("[+] GOOD: Return > 10%")

    print("\n" + "="*80)
    print("COMPARISON: Uptrend vs Volatile Period")
    print("="*80)
    print("2023-2026 (Uptrend):     SPY 50% WR, -25.77% return")
    print("2020-2022 (Volatile):    Testing now...")
    print("\nExpected: Volatile period should show MUCH better results")
    print("="*80)


if __name__ == "__main__":
    main()
