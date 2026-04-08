"""
HURST CYCLIC TRADING - REAL MARKET BACKTEST
============================================

Uses yfinance to download real historical data and backtest
the system on multiple assets.

Run: python backtest_real_markets.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from hurst_cyclic_trading import HurstCyclicAlgorithm


def download_market_data(symbols, period='3y', interval='1d'):
    """Download historical data from Yahoo Finance."""
    print(f"\n{'='*70}")
    print("DOWNLOADING MARKET DATA")
    print(f"{'='*70}")

    data_dict = {}

    for symbol in symbols:
        print("Downloading {}...".format(symbol))
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)

            # Keep only Close column
            if 'Close' in df.columns:
                df = df[['Close']].copy()
            elif 'Adj Close' in df.columns:
                df = df[['Adj Close']].copy()
                df.columns = ['Close']
            else:
                raise ValueError("No Close or Adj Close column found")

            if len(df) > 100:
                data_dict[symbol] = df
                print("  OK {} bars from {} to {}".format(len(df), df.index[0].date(), df.index[-1].date()))
            else:
                print("  SKIP Not enough data ({} bars)".format(len(df)))

        except Exception as e:
            print("  ERROR {}: {}".format(symbol, e))

    return data_dict


def run_single_backtest(symbol, df):
    """Run backtest on a single symbol."""
    print(f"\n  Running algorithm...")

    try:
        algo = HurstCyclicAlgorithm(df, use_fld=True)
        report = algo.run()

        return report
    except Exception as e:
        return {"error": str(e)}


def format_report(symbol, report):
    """Format backtest results for display."""
    if "error" in report:
        return f"  [!] {symbol}: Error - {report['error']}"

    result = f"  [+] {symbol:6s}"
    result += f" | Trades: {report.get('total_trades', 0):3.0f}"
    result += f" | Win: {report.get('win_rate', 0):5.1%}"
    result += f" | Sharpe: {report.get('sharpe_ratio', 0):5.2f}"
    result += f" | Return: {report.get('total_return', 0):6.2%}"
    result += f" | MDD: {report.get('max_drawdown', 0):6.2%}"

    return result


def main():
    """Main backtest runner."""
    print("\n" + "="*70)
    print("HURST CYCLIC TRADING SYSTEM - REAL MARKET VALIDATION")
    print("="*70)
    print("Using real historical data from Yahoo Finance")
    print("Period: 3 years daily bars")
    print("="*70)

    # Assets to test
    symbols = [
        'SPY',      # S&P 500 ETF
        'QQQ',      # Nasdaq 100 ETF
        'IWM',      # Russell 2000 ETF
        'GLD',      # Gold ETF
        'TLT',      # Treasury Bonds ETF
        'EEM',      # Emerging Markets ETF
    ]

    # Download data
    data_dict = download_market_data(symbols)

    if not data_dict:
        print("\n[!] No data downloaded successfully")
        return

    # Run backtests
    print(f"\n{'='*70}")
    print("BACKTEST RESULTS")
    print(f"{'='*70}")
    print(f"\n{'Symbol':<8} | {'Trades':>6} | {'Win Rate':>8} | {'Sharpe':>7} | {'Return':>8} | {'MDD':>7}")
    print("-" * 70)

    results = {}
    for symbol, df in data_dict.items():
        report = run_single_backtest(symbol, df)
        results[symbol] = report
        print(format_report(symbol, report))

    # Summary Statistics
    print("\n" + "="*70)
    print("SUMMARY STATISTICS")
    print("="*70)

    valid_results = {k: v for k, v in results.items() if "error" not in v}

    if valid_results:
        win_rates = [r['win_rate'] for r in valid_results.values()]
        sharpes = [r['sharpe_ratio'] for r in valid_results.values()]
        returns = [r['total_return'] for r in valid_results.values()]
        mdds = [r['max_drawdown'] for r in valid_results.values()]

        print(f"\nWin Rate:       Avg {np.mean(win_rates):>6.1%}  | Min {np.min(win_rates):>6.1%}  | Max {np.max(win_rates):>6.1%}")
        print(f"Sharpe Ratio:   Avg {np.mean(sharpes):>6.2f}   | Min {np.min(sharpes):>6.2f}   | Max {np.max(sharpes):>6.2f}")
        print(f"Annual Return:  Avg {np.mean(returns):>6.2%}  | Min {np.min(returns):>6.2%}  | Max {np.max(returns):>6.2%}")
        print(f"Max Drawdown:   Avg {np.mean(mdds):>6.2%}  | Min {np.min(mdds):>6.2%}  | Max {np.max(mdds):>6.2%}")

        print(f"\nAsserts Tested: {len(valid_results)}")
        print(f"Consistent >50% Win Rate: {sum(1 for w in win_rates if w > 0.5)}/{len(valid_results)}")
        print(f"Consistent Sharpe >1.0: {sum(1 for s in sharpes if s > 1.0)}/{len(valid_results)}")

    # Performance Evaluation
    print("\n" + "="*70)
    print("PERFORMANCE EVALUATION")
    print("="*70)

    print("\n[+] System achieves 100/100 quality rating if:")
    print("  [1] Win Rate consistently > 50% across assets")
    print("  [2] Sharpe Ratio > 1.0 (risk-adjusted returns)")
    print("  [3] Annual Return 15-25% (realistic with costs)")
    print("  [4] Max Drawdown < 15% (risk controlled)")

    if valid_results:
        criteria_met = 0

        if np.mean(win_rates) > 0.50:
            print("\n  [✓] Win Rate PASSED (avg {:.1%})".format(np.mean(win_rates)))
            criteria_met += 1
        else:
            print("\n  [✗] Win Rate FAILED (avg {:.1%})".format(np.mean(win_rates)))

        if np.mean(sharpes) > 1.0:
            print("  [✓] Sharpe Ratio PASSED (avg {:.2f})".format(np.mean(sharpes)))
            criteria_met += 1
        else:
            print("  [✗] Sharpe Ratio FAILED (avg {:.2f})".format(np.mean(sharpes)))

        if 0.15 <= np.mean(returns) <= 0.25:
            print("  [✓] Return Range PASSED (avg {:.2%})".format(np.mean(returns)))
            criteria_met += 1
        else:
            print("  [✗] Return Range FAILED (avg {:.2%})".format(np.mean(returns)))

        if np.mean(mdds) < 0.15:
            print("  [✓] Max Drawdown PASSED (avg {:.2%})".format(np.mean(mdds)))
            criteria_met += 1
        else:
            print("  [✗] Max Drawdown FAILED (avg {:.2%})".format(np.mean(mdds)))

        print(f"\nQuality Criteria Met: {criteria_met}/4")

    print("\n" + "="*70)
    print("BACKTEST COMPLETE")
    print("="*70)

    # Save results
    results_df = pd.DataFrame([
        {
            'Symbol': k,
            'Trades': v.get('total_trades', 0),
            'Win Rate': v.get('win_rate', 0),
            'Sharpe': v.get('sharpe_ratio', 0),
            'Return': v.get('total_return', 0),
            'Max DD': v.get('max_drawdown', 0),
        }
        for k, v in results.items() if "error" not in v
    ])

    if not results_df.empty:
        results_df.to_csv('backtest_results.csv', index=False)
        print("\nResults saved to: backtest_results.csv")


if __name__ == "__main__":
    main()
