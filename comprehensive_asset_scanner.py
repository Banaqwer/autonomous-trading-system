"""
COMPREHENSIVE GLOBAL ASSET SCANNER
============================================================================

Test Hurst system on ALL available major assets:
- US Equities (large-cap, mid-cap, small-cap)
- US ETFs (sector, commodity, international, bonds)
- International Stocks (major indices)
- Commodities (futures/ETFs)
- Bonds/Fixed Income
- Crypto (major coins)
- Currency (major pairs via ETFs)

Rank by:
1. Signal Frequency (trades per year) - PRIMARY
2. Win Rate (target: 65%+) - REQUIRED
3. Risk/Reward Ratio (target: 2.0:1+) - IMPORTANT
4. Sharpe Ratio (target: 1.0+) - NICE TO HAVE

Goal: Identify TOP 10-20 assets for Phase 1A deployment
Requirement: 15+ signals per year (daily trading possible)

Run: python comprehensive_asset_scanner.py
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


class ComprehensiveAssetScanner:
    """Scan all major assets for Hurst system suitability"""

    def __init__(self):
        self.results = []

        # Comprehensive asset universe
        self.asset_list = {
            # US Large Cap Equities
            'AAPL': 'Apple',
            'MSFT': 'Microsoft',
            'GOOGL': 'Google',
            'AMZN': 'Amazon',
            'NVDA': 'Nvidia',
            'META': 'Meta',
            'TSLA': 'Tesla',
            'BRK.B': 'Berkshire',
            'JNJ': 'Johnson & Johnson',
            'V': 'Visa',

            # US Mid/Small Cap
            'IWM': 'Russell 2000 (Small Cap)',
            'IJH': 'Mid Cap',
            'IVV': 'Large Cap',

            # US Sector ETFs
            'XLK': 'Technology Sector',
            'XLV': 'Healthcare Sector',
            'XLF': 'Financial Sector',
            'XLE': 'Energy Sector',
            'XLI': 'Industrial Sector',
            'XLY': 'Consumer Discretionary',
            'XLP': 'Consumer Staples',
            'XLRE': 'Real Estate',
            'XLU': 'Utilities',
            'XLRE': 'Materials',

            # US Broad Indices
            'SPY': 'S&P 500',
            'QQQ': 'Nasdaq 100',
            'IWM': 'Russell 2000',
            'VTI': 'Total US Market',
            'VTV': 'Value',
            'VUG': 'Growth',

            # International ETFs
            'EEM': 'Emerging Markets',
            'EWJ': 'Japan',
            'EWG': 'Germany',
            'EWU': 'UK',
            'EWH': 'Hong Kong',
            'EWA': 'Australia',
            'EWC': 'Canada',
            'FXI': 'China',
            'IEMG': 'Emerging Markets (broad)',
            'VXUS': 'International Stocks',

            # Bonds & Fixed Income
            'TLT': 'Long-term Treasuries',
            'IEF': 'Intermediate Treasuries',
            'SHY': 'Short-term Treasuries',
            'LQD': 'Investment Grade Corporate',
            'HYG': 'High Yield Bonds',
            'MUB': 'Municipal Bonds',
            'AGG': 'Bond Aggregate',

            # Commodities & Inflation
            'GLD': 'Gold',
            'SLV': 'Silver',
            'USO': 'Oil',
            'UNG': 'Natural Gas',
            'DBC': 'Commodity Index',
            'DBE': 'Energy',
            'DBC': 'Commodities',
            'PDBC': 'Commodity Futures',
            'GSG': 'Commodity ETF',

            # Real Assets & Alternative
            'REET': 'Real Estate',
            'VNQ': 'Real Estate ETF',
            'DIG': 'Oil & Gas Exploration',
            'XME': 'Metals & Mining',
            'DBB': 'Industrial Metals',

            # Currency (via ETFs)
            'FXY': 'Yen',
            'FXE': 'Euro',
            'FXB': 'British Pound',
            'FXA': 'Australian Dollar',
            'FXC': 'Canadian Dollar',

            # Volatility & Risk
            'VXX': 'VIX ETN',
            'UVXY': 'Ultra VIX',

            # Crypto (via ETFs - if available)
            # 'GBTC': 'Bitcoin Trust',  # If available
            # 'ETHE': 'Ethereum Trust',  # If available
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

            # Skip if no trades (no edge data)
            if num_trades == 0:
                return None

            win_rate = algo.report.get('win_rate', 0)

            # Skip if win rate below 50% (no edge)
            if win_rate < 0.50:
                return None

            total_return = algo.report.get('total_return_pct', 0) / 100
            max_dd = algo.report.get('max_drawdown', 0)
            sharpe = algo.report.get('sharpe_ratio', 0)

            # Calculate signal frequency (trades per year)
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
                'period': f"{start_date} to {end_date}",
                'num_trades': num_trades,
                'trades_per_year': trades_per_year,
                'win_rate': win_rate,
                'total_return': total_return,
                'max_drawdown': max_dd,
                'sharpe_ratio': sharpe,
                'profit_factor': profit_factor,
                'bars': len(data)
            }

            return result

        except Exception as e:
            sys.stdout = old_stdout
            return None

    def run_comprehensive_scan(self):
        """Scan all assets"""

        print("\n" + "="*100)
        print("COMPREHENSIVE GLOBAL ASSET SCANNER")
        print("Testing Hurst system on ALL major asset classes")
        print("="*100)

        # Use recent period for high signal frequency
        start_date = '2023-01-01'
        end_date = '2024-12-31'

        print(f"\nTesting {len(self.asset_list)} assets")
        print(f"Period: {start_date} to {end_date} (2 years)")
        print("\nScanning...\n")

        total_tested = 0
        successful = 0

        for symbol, name in self.asset_list.items():
            total_tested += 1
            result = self.test_asset(symbol, start_date, end_date)

            if result:
                successful += 1
                self.results.append(result)

                # Print progress
                print(f"[OK] {symbol:8} ({name:35}): {result['num_trades']:3} trades | "
                      f"WR={result['win_rate']:.1%} | Trades/Yr={result['trades_per_year']:.1f} | "
                      f"Ret={result['total_return']:.2%}")
            else:
                print(f"[NO] {symbol:8} ({name:35}): No valid data or signals")

        print("\n" + "="*100)
        print(f"Scan Complete: {successful}/{total_tested} assets found with edge")
        print("="*100)

        self.generate_comprehensive_report()

    def generate_comprehensive_report(self):
        """Generate ranking report"""

        if not self.results:
            print("ERROR: No results")
            return

        results_df = pd.DataFrame(self.results)

        # Filter for operational requirements
        operational = results_df[
            (results_df['win_rate'] >= 0.65) &  # Require 65%+ win rate
            (results_df['trades_per_year'] >= 15)  # Require 15+ trades/year for Phase 1A
        ].copy()

        print("\n\nFILTERED RESULTS (Win Rate ≥ 65%, Trades ≥ 15/year)")
        print("="*100)

        # Rank by signal frequency (primary metric)
        operational = operational.sort_values('trades_per_year', ascending=False)

        print("\nRanked by Signal Frequency (Operational Priority):\n")

        if len(operational) > 0:
            for idx, row in operational.head(20).iterrows():
                print(f"{row['symbol']:8} | {row['name']:35} | "
                      f"Trades/Yr: {row['trades_per_year']:5.1f} | "
                      f"WR: {row['win_rate']:5.1%} | "
                      f"Ret: {row['total_return']:7.2%} | "
                      f"Sharpe: {row['sharpe_ratio']:5.2f} |")
        else:
            print("No assets meet operational criteria (Win Rate 65%+, 15+ trades/year)")

        # All results ranked by win rate
        print("\n\nAll Results Ranked by Win Rate (Edge Quality):\n")

        all_sorted = results_df.sort_values('win_rate', ascending=False)

        for idx, row in all_sorted.head(30).iterrows():
            meets_freq = "[YES]" if row['trades_per_year'] >= 15 else "[LOW]"
            print(f"{meets_freq} {row['symbol']:8} | {row['name']:35} | "
                  f"Trades/Yr: {row['trades_per_year']:5.1f} | "
                  f"WR: {row['win_rate']:5.1%} | "
                  f"Ret: {row['total_return']:7.2%}")

        # Analysis
        print("\n\nKEY FINDINGS:")
        print("="*100)

        print(f"\nTotal Assets Tested: {len(results_df)}")
        print(f"Assets with Edge (WR ≥ 65%): {len(results_df[results_df['win_rate'] >= 0.65])}")
        print(f"Assets Operational for Phase 1A: {len(operational)}")

        if len(operational) > 0:
            print(f"\nTop 5 Assets by Signal Frequency:")
            for idx, (i, row) in enumerate(operational.head(5).iterrows(), 1):
                print(f"  {idx}. {row['symbol']:8} - {row['trades_per_year']:.1f} trades/year, {row['win_rate']:.1%} WR")

        # Asset class analysis
        print(f"\n\nASSET CLASS ANALYSIS:")
        print("-"*100)

        for asset_class, symbols in [
            ('Large Cap Equities', ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']),
            ('ETF Indices', ['SPY', 'QQQ', 'IWM']),
            ('Sector ETFs', ['XLK', 'XLV', 'XLF']),
            ('International', ['EEM', 'EWJ', 'FXI']),
            ('Bonds', ['TLT', 'LQD', 'HYG']),
            ('Commodities', ['GLD', 'SLV', 'USO']),
        ]:
            class_results = results_df[results_df['symbol'].isin(symbols)]
            if len(class_results) > 0:
                avg_wr = class_results['win_rate'].mean()
                avg_freq = class_results['trades_per_year'].mean()
                print(f"{asset_class:20}: Avg WR={avg_wr:.1%}, Avg Freq={avg_freq:.1f} trades/yr")

        # Recommendations
        print(f"\n\nRECOMMENDATIONS FOR PHASE 1A:")
        print("-"*100)

        if len(operational) > 0:
            print(f"\n[DEPLOY] THESE ASSETS (High Edge + High Frequency):")
            for idx, (i, row) in enumerate(operational.head(10).iterrows(), 1):
                print(f"  {idx}. {row['symbol']:8} - {row['trades_per_year']:.1f} trades/yr, {row['win_rate']:.1%} WR, "
                      f"{row['total_return']:.2%} return")

            print(f"\n[SIGNALS] Expected Signal Generation:")
            avg_trades_per_month = operational['trades_per_year'].mean() / 12
            print(f"   Average: {avg_trades_per_month:.1f} signals/month")
            print(f"   = {avg_trades_per_month/4.33:.1f} signals/week")
            print(f"   = {avg_trades_per_month/22:.2f} signals/trading day")

        else:
            print(f"\n[WARN] NO ASSETS meet operational requirements (65%+ WR + 15+ trades/year)")
            print(f"Next best:")
            for idx, (i, row) in enumerate(results_df.sort_values('trades_per_year', ascending=False).head(5).iterrows(), 1):
                freq_gap = 15 - row['trades_per_year']
                wr_gap = 0.65 - row['win_rate']
                print(f"  {idx}. {row['symbol']:8}: Missing {freq_gap:.1f} trades/yr, WR {wr_gap:.1%} below target")

        print("\n" + "="*100)
        print("ASSET SCAN COMPLETE")
        print("="*100)


def main():
    scanner = ComprehensiveAssetScanner()
    scanner.run_comprehensive_scan()


if __name__ == '__main__':
    main()
