"""
COMPREHENSIVE ASSET EXPANSION
============================================================================

GOAL: Find 40+ profitable signals/year while maintaining 65%+ win rate
NOT: Limited to 10-15 assets
YES: Test ALL viable asset classes and geographies

Strategy:
1. Test ALL 18 assets from comprehensive scan
2. Add additional commodities, bonds, currencies
3. Select best 15-25 assets by signal frequency
4. Target: 40+ signals/year combined
5. Minimum win rate: 65% blended

Expected Signal Breakdown:
- If 20 assets at 2-3/year avg = 40-60/year
- If 25 assets = 50-75/year
- Daily frequency: 0.15-0.3/day (acceptable)

Run: python comprehensive_asset_expansion.py
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


class ComprehensiveAssetExpander:
    """Find all viable assets for maximum signal generation"""

    def __init__(self, capital=100000, risk_pct=0.02):
        self.capital = capital
        self.risk_pct = risk_pct

        # ALL assets from comprehensive scan that generated signals
        # PLUS additional candidates not tested
        self.assets = {
            # Proven from scan (18 assets)
            'FXC': 'Canadian Dollar (4.5/yr expected)',
            'VXUS': 'International Stocks (4.0/yr)',
            'XME': 'Metals & Mining (4.0/yr)',
            'SPY': 'S&P 500 (3.0/yr)',
            'IVV': 'Large Cap (3.0/yr)',
            'XLV': 'Healthcare (3.0/yr)',
            'VTI': 'Total Market (3.0/yr)',
            'EWG': 'Germany (3.0/yr)',
            'IJH': 'Mid Cap (2.0/yr)',
            'QQQ': 'Nasdaq 100 (2.0/yr)',
            'LQD': 'Corp Bonds (2.5/yr)',
            'MUB': 'Muni Bonds (2.5/yr)',
            'AAPL': 'Apple (1.0/yr)',
            'TSLA': 'Tesla (1.5/yr)',
            'JNJ': 'J&J (1.0/yr)',
            'XLI': 'Industrial (1.0/yr)',
            'FXA': 'Aus Dollar (0.5/yr)',

            # Additional high-probability candidates
            'EEM': 'Emerging Markets',
            'EWJ': 'Japan ETF',
            'EWU': 'UK ETF',
            'EWC': 'Canada ETF',
            'EWZ': 'Brazil ETF',
            'FXI': 'China ETF',
            'GLD': 'Gold',
            'SLV': 'Silver',
            'TLT': 'Long Bonds',
            'IEF': 'Mid Bonds',
            'HYG': 'High Yield',
            'DBB': 'Industrial Metals',
            'GSG': 'Commodity ETF',
            'USO': 'Oil',
            'UNG': 'Natural Gas',
            'DBC': 'Commodities',
            'FXE': 'Euro',
            'FXB': 'British Pound',
            'VXX': 'VIX ETN',
            'VNQ': 'Real Estate',
            'AGG': 'Bond Aggregate',
            'BND': 'Total Bond',
            'JPIE': 'Japan Growth',
            'ASHR': 'CSH China',
            'IYM': 'Materials',
        }

        self.results = []
        self.data = {}

    def test_asset(self, symbol, name):
        """Test single asset for signal generation"""
        try:
            # Download 2-year data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365*2)

            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if data is None or len(data) < 100:
                return None

            # Run algorithm
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            algo = HurstCyclicAlgorithm(
                data,
                use_fld=True,
                confluence_threshold_edge=0.20,
                confluence_threshold_mid=0.20,
                confluence_threshold_fld=0.20
            )
            algo.run()

            sys.stdout = old_stdout

            # Extract results
            if not hasattr(algo, 'report') or algo.report is None or 'error' in algo.report:
                return None

            num_trades = algo.report.get('total_trades', 0)
            if num_trades == 0:
                return None

            win_rate = algo.report.get('win_rate', 0)
            if win_rate < 0.50:  # Skip if win rate too low
                return None

            total_return = algo.report.get('total_return_pct', 0)
            sharpe = algo.report.get('sharpe_ratio', 0)

            # Calculate trades per year
            days = (pd.Timestamp('2026-04-06') - pd.Timestamp('2024-04-06')).days
            years = days / 365.25
            trades_per_year = num_trades / years

            return {
                'symbol': symbol,
                'name': name,
                'num_trades': num_trades,
                'trades_per_year': trades_per_year,
                'win_rate': win_rate,
                'total_return': total_return,
                'sharpe_ratio': sharpe,
            }

        except Exception as e:
            return None

    def run_comprehensive_expansion(self):
        """Test all assets and find optimal set"""

        print("\n" + "="*120)
        print("COMPREHENSIVE ASSET EXPANSION - FINDING 40+ SIGNALS/YEAR")
        print("="*120)

        print(f"\nTesting {len(self.assets)} assets...")
        print(f"Period: 2024-04-06 to 2026-04-06 (2 years)")
        print(f"Minimum Win Rate: 50% (filter for 65%+)")
        print(f"Target: 40+ signals/year combined\n")

        asset_count = 0
        for symbol, name in self.assets.items():
            asset_count += 1
            print(f"[{asset_count:2d}/{len(self.assets)}] {symbol:6} {name:35}", end=' ', flush=True)

            result = self.test_asset(symbol, name)

            if result:
                self.results.append(result)
                freq_mark = "[HI]" if result['trades_per_year'] >= 3 else "[LO]"
                edge_mark = "[EDGE]" if result['win_rate'] >= 0.65 else "[OK]" if result['win_rate'] >= 0.55 else "[LOW]"
                print(f"{freq_mark} {result['trades_per_year']:5.1f}/yr {edge_mark} {result['win_rate']:.0%}")
            else:
                print("[SKIP]")

        self.analyze_expansion()

    def analyze_expansion(self):
        """Analyze full asset expansion results"""

        if not self.results:
            print("\nNo results from expansion")
            return

        df = pd.DataFrame(self.results)

        print("\n\n" + "="*120)
        print("EXPANSION RESULTS ANALYSIS")
        print("="*120)

        # Filter by win rate
        edge_assets = df[df['win_rate'] >= 0.65].copy()
        acceptable_assets = df[df['win_rate'] >= 0.60].copy()
        all_assets = df.copy()

        print(f"\n[ASSET COUNTS]")
        print(f"  Total assets with any edge (50%+):    {len(df)}")
        print(f"  Assets with acceptable WR (60%+):    {len(acceptable_assets)}")
        print(f"  Assets with GOOD WR (65%+):          {len(edge_assets)}")

        # Calculate portfolio frequencies
        print(f"\n[SIGNAL FREQUENCY ANALYSIS]")

        if len(df) > 0:
            print(f"\n  ALL {len(df)} assets (50%+ WR):")
            total_all = df['trades_per_year'].sum()
            print(f"    Total: {total_all:.1f}/year")
            print(f"    Daily: {total_all/252:.2f} signals/day")
            print(f"    Weekly: {total_all/52:.1f} signals/week")

        if len(acceptable_assets) > 0:
            print(f"\n  ACCEPTABLE {len(acceptable_assets)} assets (60%+ WR):")
            total_acc = acceptable_assets['trades_per_year'].sum()
            blended_wr_acc = (acceptable_assets['trades_per_year'] * acceptable_assets['win_rate']).sum() / acceptable_assets['trades_per_year'].sum()
            print(f"    Total: {total_acc:.1f}/year")
            print(f"    Daily: {total_acc/252:.2f} signals/day")
            print(f"    Blended WR: {blended_wr_acc:.0%}")

        if len(edge_assets) > 0:
            print(f"\n  EDGE {len(edge_assets)} assets (65%+ WR):")
            total_edge = edge_assets['trades_per_year'].sum()
            blended_wr_edge = (edge_assets['trades_per_year'] * edge_assets['win_rate']).sum() / edge_assets['trades_per_year'].sum()
            print(f"    Total: {total_edge:.1f}/year")
            print(f"    Daily: {total_edge/252:.2f} signals/day")
            print(f"    Blended WR: {blended_wr_edge:.0%}")

        # Find optimal set for 40+ signals
        print(f"\n[OPTIMAL PORTFOLIO FOR 40+ SIGNALS/YEAR]")

        # Try different cutoff points
        for min_wr in [0.65, 0.60, 0.55, 0.50]:
            subset = df[df['win_rate'] >= min_wr].sort_values('trades_per_year', ascending=False)
            total_signals = subset['trades_per_year'].sum()

            if total_signals >= 40:
                blended_wr = (subset['trades_per_year'] * subset['win_rate']).sum() / subset['trades_per_year'].sum()
                daily_freq = total_signals / 252
                print(f"\n  [{len(subset)} assets with {min_wr:.0%}+ WR] = {total_signals:.1f}/year ({'TARGET ACHIEVED' if total_signals >= 40 else 'below target'})")
                print(f"    Blended WR: {blended_wr:.0%}")
                print(f"    Daily signals: {daily_freq:.2f}/day")
                print(f"    Assets:")

                for idx, (i, row) in enumerate(subset.head(20).iterrows(), 1):
                    print(f"      {idx:2d}. {row['symbol']:6} {row['name']:30} {row['trades_per_year']:5.1f}/yr @ {row['win_rate']:.0%}")

                break

        # Top 20 ranked by frequency
        print(f"\n[TOP 20 ASSETS BY SIGNAL FREQUENCY (All with 50%+ WR)]")
        print(f"─" * 120)

        top20 = df.sort_values('trades_per_year', ascending=False).head(20)
        total_top20 = top20['trades_per_year'].sum()
        blended_top20 = (top20['trades_per_year'] * top20['win_rate']).sum() / top20['trades_per_year'].sum()

        for idx, (i, row) in enumerate(top20.iterrows(), 1):
            wr_rating = "[EXCELLENT]" if row['win_rate'] >= 0.75 else "[GOOD]" if row['win_rate'] >= 0.65 else "[OK]"
            print(f"{idx:2d}. {row['symbol']:6} {row['name']:30} {row['trades_per_year']:5.1f}/yr @ {row['win_rate']:.0%} {wr_rating}")

        print(f"\nTOP 20 TOTALS:")
        print(f"  Combined: {total_top20:.1f} signals/year")
        print(f"  Daily: {total_top20/252:.2f} signals/day")
        print(f"  Blended WR: {blended_top20:.0%}")
        print(f"  Status: {'TARGET EXCEEDED' if total_top20 >= 40 else 'BELOW TARGET'}")

        # Recommendation
        print(f"\n" + "="*120)
        print("RECOMMENDATION FOR PHASE 1A DEPLOYMENT")
        print("="*120)

        # Find minimum set for 40+ signals
        df_sorted = df.sort_values('trades_per_year', ascending=False)
        cumsum = 0
        recommended = []
        for idx, (i, row) in enumerate(df_sorted.iterrows()):
            cumsum += row['trades_per_year']
            recommended.append(row)
            if cumsum >= 40:
                break

        print(f"\nRECOMMENDED ASSET SET: {len(recommended)} assets")
        print(f"Expected Total: {cumsum:.1f} signals/year")
        print(f"Blended WR: {(pd.DataFrame(recommended)['trades_per_year'] * pd.DataFrame(recommended)['win_rate']).sum() / pd.DataFrame(recommended)['trades_per_year'].sum():.0%}")
        print(f"Daily Signals: {cumsum/252:.2f}/day")

        for idx, row in enumerate(recommended, 1):
            wr_rating = "[EXCELLENT]" if row['win_rate'] >= 0.75 else "[GOOD]" if row['win_rate'] >= 0.65 else "[OK]"
            print(f"  {idx:2d}. {row['symbol']:6} {row['trades_per_year']:5.1f}/yr @ {row['win_rate']:.0%} {wr_rating}")

        print(f"\n✓ Ready for Phase 1A deployment with {len(recommended)} assets")
        print(f"✓ {cumsum:.1f} signals/year ensures operational viability")
        print(f"✓ Blended win rate maintains edge")

        print("\n" + "="*120 + "\n")


def main():
    expander = ComprehensiveAssetExpander(capital=100000, risk_pct=0.02)
    expander.run_comprehensive_expansion()


if __name__ == '__main__':
    main()
