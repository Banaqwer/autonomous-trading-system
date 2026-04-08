"""
QUICK ASSET SCANNER
============================================================================

Quick test of top 20 candidates to identify optimal assets for Phase 1A
Tests on 2023-2024 data (2 years)

Priority:
1. Assets with high signal frequency (15+ trades/year minimum)
2. Win rate >= 65%
3. Good Sharpe ratio for risk-adjusted returns

Run: python quick_asset_scanner.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class QuickAssetScanner:
    """Quick scan of top candidate assets"""

    def __init__(self):
        self.results = []

        # Top candidates based on known characteristics
        self.asset_list = {
            # High signal-generation assets (daily timeframe)
            'IWM': 'Russell 2000 (High vol, high signal)',
            'EEM': 'Emerging Markets (High vol, high signal)',
            'SPY': 'S&P 500 (Most liquid, baseline)',
            'QQQ': 'Nasdaq 100 (High vol tech)',
            'GLD': 'Gold (Commodity, lower vol)',

            # International
            'EWJ': 'Japan (Developed market)',
            'FXI': 'China (Emerging)',

            # Bonds
            'TLT': 'Long-term Treasuries',
            'HYG': 'High Yield Bonds',

            # More sector/thematic
            'XLK': 'Technology Sector',
            'XLE': 'Energy Sector',
            'VNQ': 'Real Estate',

            # Volatility indicators
            'VXX': 'VIX ETN',
            'UVXY': 'Ultra VIX',

            # Additional commodity/currency
            'SLV': 'Silver',
            'USO': 'Oil',
            'FXY': 'Yen (Currency)',
            'FXE': 'Euro (Currency)',
            'DBC': 'Commodities',
            'DBB': 'Industrial Metals',
        }

    def test_asset(self, symbol, start_date, end_date):
        """Test Hurst system on specific asset"""
        try:
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            # Download data
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if data.empty or len(data) < 50:
                sys.stdout = old_stdout
                return None

            # Run algorithm
            algo = HurstCyclicAlgorithm(data, use_fld=True)
            report = algo.run()

            sys.stdout = old_stdout

            # Extract results
            if not hasattr(algo, 'report'):
                return None

            num_trades = algo.report.get('total_trades', 0)

            # Skip if no trades
            if num_trades == 0:
                return None

            win_rate = algo.report.get('win_rate', 0)

            # Skip if win rate too low
            if win_rate < 0.50:
                return None

            total_return = algo.report.get('total_return_pct', 0) / 100
            max_dd = algo.report.get('max_drawdown', 0)
            sharpe = algo.report.get('sharpe_ratio', 0)

            # Calculate signal frequency
            days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
            years = days / 365.25
            trades_per_year = num_trades / years if years > 0 else 0

            # Calculate profit factor
            trades = getattr(algo, 'trades', [])
            winning = [t.pnl for t in trades if t.pnl > 0]
            losing = [t.pnl for t in trades if t.pnl < 0]

            if losing:
                profit_factor = abs(sum(winning)) / abs(sum(losing)) if sum(losing) != 0 else 0
            else:
                profit_factor = 999 if winning else 0

            result = {
                'symbol': symbol,
                'name': self.asset_list.get(symbol, symbol),
                'num_trades': num_trades,
                'trades_per_year': trades_per_year,
                'win_rate': win_rate,
                'total_return': total_return,
                'max_drawdown': max_dd,
                'sharpe_ratio': sharpe,
                'profit_factor': profit_factor,
            }

            return result

        except Exception as e:
            sys.stdout = old_stdout
            return None

    def run_scan(self):
        """Scan top candidates"""

        print("\n" + "="*100)
        print("QUICK ASSET SCANNER - TOP 20 CANDIDATES")
        print("Testing for Phase 1A operability (signal frequency + edge)")
        print("="*100)

        start_date = '2023-01-01'
        end_date = '2024-12-31'

        print(f"\nTesting {len(self.asset_list)} assets")
        print(f"Period: {start_date} to {end_date} (2 years)")
        print("\nScanning...\n")

        for symbol, name in self.asset_list.items():
            result = self.test_asset(symbol, start_date, end_date)

            if result:
                self.results.append(result)
                freq_indicator = "[HI-FREQ]" if result['trades_per_year'] >= 15 else "[LOW-FREQ]"
                edge_indicator = "[EDGE]" if result['win_rate'] >= 0.65 else "[WEAK]"

                print(f"[OK] {symbol:8} | {freq_indicator} {edge_indicator} | "
                      f"{result['num_trades']:3} trades ({result['trades_per_year']:5.1f}/yr) | "
                      f"WR={result['win_rate']:.1%} | "
                      f"Return={result['total_return']:7.2%} | "
                      f"Sharpe={result['sharpe_ratio']:5.2f}")
            else:
                print(f"[NO] {symbol:8} | No valid edge data")

        self.generate_report()

    def generate_report(self):
        """Generate ranking report"""

        if not self.results:
            print("\nERROR: No results from scan")
            return

        results_df = pd.DataFrame(self.results)

        # Filter for operational requirements
        operational = results_df[
            (results_df['win_rate'] >= 0.65) &
            (results_df['trades_per_year'] >= 15)
        ].copy()

        print("\n\n" + "="*100)
        print("SCAN RESULTS SUMMARY")
        print("="*100)

        print("\n[OPERATIONAL ASSETS] Win Rate >= 65% AND Trades >= 15/year:")
        print("-"*100)

        if len(operational) > 0:
            operational_sorted = operational.sort_values('trades_per_year', ascending=False)
            for idx, row in operational_sorted.iterrows():
                print(f"{row['symbol']:8} | {row['name']:35} | "
                      f"Trades/Yr: {row['trades_per_year']:5.1f} | "
                      f"WR: {row['win_rate']:5.1%} | "
                      f"Return: {row['total_return']:7.2%} | "
                      f"PF: {row['profit_factor']:4.2f}")
        else:
            print("NO ASSETS meet operational requirements")

        # All results ranked by win rate
        print("\n\n[ALL RESULTS] Ranked by Win Rate:")
        print("-"*100)

        all_sorted = results_df.sort_values('win_rate', ascending=False)
        for idx, row in all_sorted.iterrows():
            freq_status = "[GOOD]" if row['trades_per_year'] >= 15 else "[LOW ]"
            print(f"{freq_status} {row['symbol']:8} | {row['name']:35} | "
                  f"Trades/Yr: {row['trades_per_year']:5.1f} | "
                  f"WR: {row['win_rate']:5.1%} | "
                  f"Return: {row['total_return']:7.2%}")

        # Analysis
        print("\n\n" + "="*100)
        print("KEY METRICS")
        print("="*100)

        print(f"\nTotal Assets Tested:              {len(results_df)}")
        print(f"Assets with Edge (WR >= 65%):   {len(results_df[results_df['win_rate'] >= 0.65])}")
        print(f"Assets with High Frequency:     {len(results_df[results_df['trades_per_year'] >= 15])}")
        print(f"Assets Operational for Phase 1A: {len(operational)}")

        if len(operational) > 0:
            print(f"\n[DEPLOYMENT RECOMMENDATIONS]")
            print(f"Use these {len(operational)} assets for Phase 1A paper trading:")

            for idx, (i, row) in enumerate(operational.sort_values('trades_per_year', ascending=False).iterrows(), 1):
                print(f"  {idx}. {row['symbol']:8} - {row['trades_per_year']:.1f} signals/yr = "
                      f"{row['trades_per_year']/252:.2f} signals/trading day")

            avg_signals_per_day = operational['trades_per_year'].mean() / 252
            print(f"\nExpected daily signal generation: {avg_signals_per_day:.1f} signals/day during Phase 1A")
            print(f"(This ensures continuous tradeable signals during paper trading validation)")

        print("\n" + "="*100)
        print("SCAN COMPLETE")
        print("="*100 + "\n")


def main():
    scanner = QuickAssetScanner()
    scanner.run_scan()


if __name__ == '__main__':
    main()
