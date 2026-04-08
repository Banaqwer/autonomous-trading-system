"""
COMPREHENSIVE ASSET DISCOVERY - YEARLY BREAKDOWN METHOD
============================================================================

Purpose: Scan 50+ assets using year-by-year analysis (proven reliable method)
to identify highest-profit opportunities for portfolio expansion.

Method: Same as 5-year validation - test each year separately, aggregate results.
This avoids computational bottleneck and provides market-regime analysis.
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


class AssetDiscoveryYearly:
    """Test 50+ assets using year-by-year approach"""

    def __init__(self):
        self.current_portfolio = {
            'USO': 'Oil',
            'TLT': 'Long Bonds',
            'MUB': 'Muni Bonds',
            'FXC': 'Canadian Dollar',
            'EWG': 'Germany',
            'IJH': 'Mid Cap',
            'VNQ': 'Real Estate',
            'DBC': 'Commodities',
            'GSG': 'Commodity ETF',
            'XLV': 'Healthcare',
            'VXX': 'VIX ETN',
            'QQQ': 'Nasdaq',
            'EWC': 'Canada ETF',
            'WEAT': 'Wheat',
            'FXE': 'Euro',
        }

        self.candidates = {
            # Indices
            'SPY': 'S&P 500',
            'IWM': 'Russell 2000',
            'VTI': 'Total US Market',
            'VTV': 'Value',
            'VUG': 'Growth',

            # International
            'EWU': 'UK',
            'EWJ': 'Japan',
            'EWA': 'Australia',
            'IEMG': 'Emerging Markets',
            'VXUS': 'Total International',

            # Bonds
            'BND': 'Broad Bond',
            'AGG': 'Aggregate Bond',
            'IEF': 'Treasury 7-10yr',
            'HYG': 'High Yield Bond',
            'LQD': 'Investment Grade',
            'SCHP': 'TIPS',

            # Metals
            'GLD': 'Gold',
            'SLV': 'Silver',
            'GDX': 'Gold Miners',

            # Commodities
            'PDBC': 'Commodities Diversified',
            'UNG': 'Natural Gas',
            'UCO': 'Oil 2x',

            # Currency
            'FXY': 'Japanese Yen',
            'FXA': 'Australian Dollar',
            'FXG': 'British Pound',

            # Sectors
            'XLF': 'Financials',
            'XLE': 'Energy',
            'XLI': 'Industrials',
            'XLU': 'Utilities',
            'XLP': 'Consumer Staples',
            'XLY': 'Consumer Discretionary',
            'XLK': 'Technology',
            'XLRE': 'Real Estate Alt',

            # Tech/Innovation
            'EMQQ': 'Emerging Market Tech',
            'SKYY': 'Cloud Computing',
            'ARKK': 'Ark Innovation',
            'ARKQ': 'Ark Autonomous Tech',
            'ARKF': 'Ark Finance',

            # Volatility
            'UVXY': 'VIX 2x',
            'SVXY': 'VIX Inverse',

            # Dividend
            'DGRO': 'Dividend Growth',
            'DVY': 'High Dividend Yield',
        }

        self.data = {}
        self.results = []

    def download_data(self):
        """Download 5-year data for candidates"""
        print("\n" + "="*120)
        print("COMPREHENSIVE ASSET DISCOVERY: 50+ CANDIDATE ASSETS")
        print("="*120)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*5)

        print(f"\nDownloading 5-year data ({start_date.date()} to {end_date.date()})...")
        print(f"Candidates: {len(self.candidates)}\n")

        successful = 0
        for symbol, name in sorted(self.candidates.items()):
            print(f"{symbol:6} {name:30}", end=' ', flush=True)
            try:
                data = yf.download(symbol, start=start_date, end=end_date, progress=False)
                if data is not None and len(data) > 1000:
                    self.data[symbol] = data
                    successful += 1
                    print(f"[OK] {len(data)} bars")
                else:
                    print(f"[SKIP]")
            except:
                print(f"[ERROR]")

        print(f"\nDownloaded: {successful}/{len(self.candidates)}")

    def backtest_all(self):
        """Yearly backtest on all assets"""
        print("\n" + "="*120)
        print("YEAR-BY-YEAR BACKTEST ON ALL CANDIDATES")
        print("="*120 + "\n")

        for symbol, name in sorted(self.candidates.items()):
            if symbol not in self.data:
                continue

            data = self.data[symbol]
            all_trades = 0
            weighted_wr = 0
            total_freq = 0
            profitable_years = 0
            years_tested = 0

            for year in range(2021, 2027):
                year_start = f'{year}-01-01'
                year_end = f'{year}-12-31'

                year_data = data[(data.index >= year_start) & (data.index <= year_end)]

                if len(year_data) < 50:
                    continue

                try:
                    old_stdout = sys.stdout
                    sys.stdout = StringIO()

                    algo = HurstCyclicAlgorithm(year_data, use_fld=True)
                    algo.run()

                    sys.stdout = old_stdout

                    if algo.report and 'error' not in algo.report:
                        trades = algo.report.get('total_trades', 0)
                        wr = algo.report.get('win_rate', 0)
                        ret = algo.report.get('total_return_pct', 0)

                        if trades > 0:
                            all_trades += trades
                            weighted_wr += wr * trades
                            total_freq += trades / 1
                            if ret > 0:
                                profitable_years += 1
                            years_tested += 1
                except:
                    sys.stdout = old_stdout

            if all_trades > 0:
                avg_wr = weighted_wr / all_trades
                avg_freq = total_freq / years_tested if years_tested > 0 else 0

                if avg_wr >= 0.60:  # Quality threshold
                    self.results.append({
                        'symbol': symbol,
                        'name': name,
                        'trades': all_trades,
                        'freq_per_year': avg_freq,
                        'win_rate': avg_wr,
                        'profitable_years': profitable_years,
                        'years_tested': years_tested,
                    })

                    status = "[OK]" if avg_wr >= 0.65 else "[WARN]"
                    print(f"{symbol:6} {name:30} {all_trades:2} trades @ {avg_wr:.0%} {status}")

    def analyze_and_rank(self):
        """Analyze results and identify top candidates"""
        print("\n" + "="*120)
        print("ANALYSIS & RANKING")
        print("="*120)

        if not self.results:
            print("\nNo assets met 60%+ win rate threshold")
            return

        df = pd.DataFrame(self.results)
        df = df.sort_values('win_rate', ascending=False)

        print(f"\nAssets with 60%+ Win Rate: {len(df)}/{len(self.candidates)}")
        print(f"\nTOP CANDIDATES (by win rate):")
        print(f"{'Rank':<5} {'Symbol':<8} {'Name':<30} {'Win Rate':<12} {'Freq/yr':<12} {'Profitable Yrs':<15}")
        print(f"{'-'*5} {'-'*8} {'-'*30} {'-'*12} {'-'*12} {'-'*15}")

        for i, (_, row) in enumerate(df.head(15).iterrows(), 1):
            print(f"{i:<5} {row['symbol']:<8} {row['name']:<30} {row['win_rate']:>10.0%}  {row['freq_per_year']:>10.1f}  {row['profitable_years']}/{row['years_tested']}")

        # Find new candidates (not in current portfolio)
        print(f"\n" + "="*120)
        print("NEW CANDIDATES FOR PORTFOLIO EXPANSION")
        print("="*120)

        new_candidates = df[~df['symbol'].isin(self.current_portfolio.keys())]

        if len(new_candidates) > 0:
            print(f"\nNew high-quality assets (60%+ WR not in current portfolio):")
            for _, row in new_candidates.head(10).iterrows():
                print(f"\n  {row['symbol']} ({row['name']})")
                print(f"    Win Rate: {row['win_rate']:.0%}")
                print(f"    Frequency: {row['freq_per_year']:.1f}/year")
                print(f"    Profitable years: {row['profitable_years']}/{row['years_tested']}")

                if row['win_rate'] >= 0.75:
                    print(f"    Recommendation: ADD TO PHASE 1A (high-edge asset)")
                elif row['win_rate'] >= 0.70:
                    print(f"    Recommendation: ADD TO PHASE 1B (solid-edge asset)")
                else:
                    print(f"    Recommendation: Monitor or add to Phase 2 (diversifier)")
        else:
            print("\nNo new candidates found. Current portfolio is well-selected.")

        # Calculate improved portfolio metrics
        if len(new_candidates) > 0:
            print(f"\n" + "="*120)
            print("PORTFOLIO EXPANSION ANALYSIS")
            print("="*120)

            top_new = new_candidates.head(5)
            current_freq = 65.5
            current_wr = 0.719

            new_freq_total = current_freq + top_new['freq_per_year'].sum()
            new_wr_blended = (current_freq * current_wr + (top_new['freq_per_year'] * top_new['win_rate']).sum()) / new_freq_total

            print(f"\nIf we add TOP 5 NEW ASSETS to the 15-asset portfolio:")
            print(f"  Current: 65.5/yr @ 71.9% WR")
            print(f"  New assets: {top_new['freq_per_year'].sum():.1f}/yr @ {top_new['win_rate'].mean():.1%} avg WR")
            print(f"  Combined: {new_freq_total:.1f}/yr @ {new_wr_blended:.1%} WR")
            print(f"\n  Expected 12-week return improvement: {(new_wr_blended - current_wr) * 100:.1f}% edge increase")


def main():
    discovery = AssetDiscoveryYearly()
    discovery.download_data()
    discovery.backtest_all()
    discovery.analyze_and_rank()

    print(f"\n" + "="*120)
    print("ASSET DISCOVERY COMPLETE")
    print("="*120 + "\n")


if __name__ == '__main__':
    main()
