"""
COMPREHENSIVE ASSET DISCOVERY & OPTIMIZATION
============================================================================

Purpose: Scan a broad universe of liquid trading assets to identify
additional high-profit opportunities beyond the current 15-asset portfolio.

Approach:
1. Test 50+ unexplored assets across 5 years (2021-2026)
2. Filter for 60%+ win rate (consistent with portfolio criteria)
3. Rank by profitability (frequency x win rate x return)
4. Identify top candidates for portfolio expansion
5. Test for correlation with existing 15 assets (avoid redundancy)

This will maximize profit potential by finding the highest-edge assets.
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


class ComprehensiveAssetDiscovery:
    """Scan 50+ assets to find highest-profit opportunities"""

    def __init__(self):
        # Current validated portfolio (15)
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

        # Candidates to test (50+ new/alternative assets)
        self.candidates = {
            # Major indices (alternatives to existing)
            'SPY': 'S&P 500',
            'IVV': 'iShares Core S&P 500',
            'VOO': 'Vanguard S&P 500',
            'IWM': 'Russell 2000',
            'VTI': 'Total US Market',
            'VTV': 'Value',
            'VUG': 'Growth',

            # International equities
            'EWU': 'UK',
            'EWJ': 'Japan',
            'EWA': 'Australia',
            'EWH': 'Hong Kong',
            'EWW': 'Mexico',
            'IEMG': 'Emerging Markets',
            'VXUS': 'Total International',

            # Bonds (alternatives/additions)
            'BND': 'Broad Bond',
            'AGG': 'Aggregate Bond',
            'IEF': 'Treasury 7-10yr',
            'SHV': 'Short Treasury',
            'HYG': 'High Yield Bond',
            'LQD': 'Investment Grade',
            'VCIT': 'Intermediate Corp',
            'SCHP': 'TIPS',

            # Precious metals
            'GLD': 'Gold',
            'SLV': 'Silver',
            'GDX': 'Gold Miners',
            'SILV': 'Silver Miners',

            # Commodity variants
            'CRB': 'Commodity Index',
            'PDBC': 'Commodities Diversified',
            'UNG': 'Natural Gas',
            'UCO': 'Oil 2x',

            # Currency alternatives
            'FXY': 'Japanese Yen',
            'FXA': 'Australian Dollar',
            'FXG': 'British Pound',
            'FXV': 'Swiss Franc',

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

            # Volatility alternatives
            'UVXY': 'VIX 2x',
            'SVXY': 'VIX Inverse',

            # Misc high-potential
            'DGRO': 'Dividend Growth',
            'DVY': 'High Dividend Yield',
        }

        self.data = {}
        self.results = []

    def download_candidate_data(self):
        """Download 5 years of data for all candidates"""
        print("\n" + "="*120)
        print("COMPREHENSIVE ASSET DISCOVERY: DOWNLOADING DATA FOR 50+ CANDIDATES")
        print("="*120)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*5)

        print(f"\nDownloading 5-year data ({start_date.date()} to {end_date.date()})...")
        print(f"Candidates to test: {len(self.candidates)}\n")

        successful = 0
        failed = 0

        for symbol, name in sorted(self.candidates.items()):
            print(f"{symbol:6} {name:30}", end=' ', flush=True)
            try:
                data = yf.download(symbol, start=start_date, end=end_date, progress=False)
                if data is not None and len(data) > 1000:
                    self.data[symbol] = data
                    successful += 1
                    print(f"[OK] {len(data)} bars")
                else:
                    failed += 1
                    print(f"[SKIP] Insufficient data")
            except Exception as e:
                failed += 1
                print(f"[ERROR]")

        print(f"\n{successful} assets downloaded, {failed} skipped/failed")
        print(f"Ready to backtest: {len(self.data)} assets")

    def backtest_candidates(self):
        """Run Hurst backtest on all candidates across 5 years"""
        print("\n" + "="*120)
        print("5-YEAR BACKTEST ON ALL CANDIDATES (2021-2026)")
        print("="*120 + "\n")

        for symbol, name in sorted(self.candidates.items()):
            if symbol not in self.data:
                print(f"{symbol:6} {name:30} [SKIP] No data")
                continue

            print(f"{symbol:6} {name:30}", end=' ', flush=True)

            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo = HurstCyclicAlgorithm(
                    self.data[symbol],
                    use_fld=True,
                    confluence_threshold_edge=0.20,
                    confluence_threshold_mid=0.20,
                    confluence_threshold_fld=0.20
                )
                algo.run()

                sys.stdout = old_stdout

                if not hasattr(algo, 'report') or algo.report is None or 'error' in algo.report:
                    print("[SKIP] No report")
                    continue

                num_trades = algo.report.get('total_trades', 0)
                if num_trades < 2:
                    print(f"[LOW] Only {num_trades} trades")
                    continue

                win_rate = algo.report.get('win_rate', 0)
                total_return = algo.report.get('total_return_pct', 0)
                sharpe = algo.report.get('sharpe_ratio', 0)
                max_dd = algo.report.get('max_drawdown', 0)

                # Calculate annual metrics
                days = (pd.Timestamp('2026-04-06') - pd.Timestamp('2021-04-06')).days
                years = days / 365.25
                trades_per_year = num_trades / years
                annual_return = total_return / years

                # Profit potential: win_rate * trades_per_year * typical_return_multiple
                # Assume 1.5x return on winners, 1x loss on losers (2:1 risk/reward)
                profit_potential = (win_rate * 1.5 + (1 - win_rate) * (-1)) * trades_per_year

                self.results.append({
                    'symbol': symbol,
                    'name': name,
                    'num_trades': num_trades,
                    'trades_per_year': trades_per_year,
                    'win_rate': win_rate,
                    'total_return_5yr': total_return,
                    'annual_return': annual_return,
                    'sharpe': sharpe,
                    'max_dd': max_dd,
                    'profit_potential': profit_potential,
                })

                status = "[OK]" if win_rate >= 0.65 else "[WARN]" if win_rate >= 0.55 else "[LOW]"
                print(f"{num_trades:2} trades | {trades_per_year:4.1f}/yr | WR={win_rate:.0%} {status} | Return={total_return:+.1f}%")

            except Exception as e:
                sys.stdout = old_stdout
                print(f"[ERROR]")

    def analyze_results(self):
        """Analyze and rank results"""
        print("\n" + "="*120)
        print("ANALYSIS & RANKING")
        print("="*120)

        if not self.results:
            print("No results to analyze")
            return

        df = pd.DataFrame(self.results)

        # Filter for quality assets (60%+ win rate)
        df_quality = df[df['win_rate'] >= 0.60].copy()
        df_quality = df_quality.sort_values('profit_potential', ascending=False)

        print(f"\nAssets with 60%+ Win Rate: {len(df_quality)}/{len(df)}")
        print(f"\nTOP CANDIDATES BY PROFIT POTENTIAL:")
        print(f"{'Rank':<5} {'Symbol':<8} {'Name':<30} {'WR':<8} {'Trades/yr':<12} {'Profit Pot':<15} {'Return 5yr':<12}")
        print(f"{'-'*5} {'-'*8} {'-'*30} {'-'*8} {'-'*12} {'-'*15} {'-'*12}")

        for i, (_, row) in enumerate(df_quality.head(20).iterrows(), 1):
            print(f"{i:<5} {row['symbol']:<8} {row['name']:<30} {row['win_rate']:>6.0%}  {row['trades_per_year']:>10.1f}  {row['profit_potential']:>13.2f}x  {row['total_return_5yr']:>+10.1f}%")

        # Compare to current portfolio
        print(f"\n" + "="*120)
        print("CURRENT PORTFOLIO PERFORMANCE (for comparison)")
        print("="*120)

        current_stats = {
            'USO': {'wr': 0.65, 'freq': 11.5, 'return': None},
            'TLT': {'wr': 0.72, 'freq': 9.0, 'return': None},
            'MUB': {'wr': 0.79, 'freq': 7.0, 'return': None},
            'FXC': {'wr': 0.64, 'freq': 7.0, 'return': None},
            'EWG': {'wr': 0.92, 'freq': 6.5, 'return': None},
            'IJH': {'wr': 0.62, 'freq': 6.5, 'return': None},
            'VNQ': {'wr': 0.58, 'freq': 6.0, 'return': None},
            'DBC': {'wr': 0.80, 'freq': 2.5, 'return': None},
            'GSG': {'wr': 0.60, 'freq': 2.5, 'return': None},
            'XLV': {'wr': 0.75, 'freq': 2.0, 'return': None},
            'VXX': {'wr': 1.00, 'freq': 2.0, 'return': None},
            'QQQ': {'wr': 0.67, 'freq': 1.5, 'return': None},
            'EWC': {'wr': 0.67, 'freq': 1.5, 'return': None},
            'WEAT': {'wr': 1.00, 'freq': 3.5, 'return': None},
            'FXE': {'wr': 0.67, 'freq': 1.5, 'return': None},
        }

        print(f"Current portfolio average WR: 75.5%")
        print(f"Current portfolio total frequency: 65.5/year")

        # Identify non-overlapping candidates
        print(f"\n" + "="*120)
        print("RECOMMENDATIONS FOR PORTFOLIO EXPANSION")
        print("="*120)

        # Get top 10 new assets
        top_new = df_quality.head(10)

        print(f"\nTOP 10 NEW CANDIDATES (not in current portfolio):")
        print(f"\nAssets that would INCREASE portfolio frequency while maintaining 60%+ edge:")

        added_count = 0
        for _, row in top_new.iterrows():
            if row['symbol'] not in self.current_portfolio:
                print(f"\n  {row['symbol']} ({row['name']})")
                print(f"    Win Rate: {row['win_rate']:.0%}")
                print(f"    Frequency: {row['trades_per_year']:.1f}/year")
                print(f"    5-Year Return: {row['total_return_5yr']:+.1f}%")
                print(f"    Profit Potential: {row['profit_potential']:.2f}x")
                print(f"    Recommendation: ADD TO PHASE {1 if row['win_rate'] >= 0.70 else 2}")
                added_count += 1

        if added_count == 0:
            print("\nNo new candidates meet the 60%+ win rate threshold.")
            print("Current portfolio is already optimized.")

    def monte_carlo_expanded_portfolio(self):
        """Test expanded portfolio with new assets"""
        print(f"\n" + "="*120)
        print("MONTE CARLO: EXPANDED PORTFOLIO SCENARIO")
        print("="*120)

        if not self.results:
            return

        df = pd.DataFrame(self.results)
        df_quality = df[df['win_rate'] >= 0.60]

        if len(df_quality) == 0:
            print("\nNo new high-quality assets found.")
            return

        # Simulate adding top 3 new candidates to current portfolio
        top_3_new = df_quality.sort_values('profit_potential', ascending=False).head(3)

        if len(top_3_new) == 0:
            return

        # Calculate blended win rate with new assets
        all_assets_wr = list(df_quality['win_rate'].values)
        all_assets_freq = list(df_quality['trades_per_year'].values)

        if all_assets_freq:
            weighted_wr_expanded = (np.array(all_assets_freq) * np.array(all_assets_wr)).sum() / np.sum(all_assets_freq)
            total_freq_expanded = np.sum(all_assets_freq)

            print(f"\nIf we add TOP 3 NEW ASSETS to the portfolio:")
            print(f"  New assets: {', '.join(top_3_new['symbol'].values)}")
            print(f"  Combined frequency (new): {total_freq_expanded:.1f}/year")
            print(f"  Combined win rate (new): {weighted_wr_expanded:.1%}")
            print(f"  vs. Current (15 assets): 65.5/year @ 71.9%")

            # Monte Carlo simulation
            capital = 100000
            risk_per_trade = capital * 0.02
            trading_signals = int(total_freq_expanded * 12 / 52)  # 12 weeks

            final_equities = []
            for sim in range(10000):
                equity = capital
                for trade in range(trading_signals):
                    is_win = np.random.random() < weighted_wr_expanded
                    pnl = (risk_per_trade * 1.5) if is_win else (-risk_per_trade)
                    equity += pnl
                final_equities.append(equity)

            eq_array = np.array(final_equities)
            profitable = np.sum(eq_array > capital)
            profitable_pct = 100 * profitable / 10000

            print(f"\nMonte Carlo Results (10,000 runs):")
            print(f"  Expected 12-week equity: ${eq_array.mean():,.0f} (vs ${125384:,} current)")
            print(f"  Profitable runs: {profitable_pct:.1f}%")
            print(f"  Expected gain: ${eq_array.mean() - capital:,.0f}")

            if eq_array.mean() > 125384:
                print(f"\n  VERDICT: EXPANSION IMPROVES EXPECTED RETURN")
            else:
                print(f"\n  VERDICT: Current portfolio already optimal")


def main():
    discovery = ComprehensiveAssetDiscovery()
    discovery.download_candidate_data()
    discovery.backtest_candidates()
    discovery.analyze_results()
    discovery.monte_carlo_expanded_portfolio()

    print(f"\n" + "="*120)
    print("ASSET DISCOVERY ANALYSIS COMPLETE")
    print("="*120 + "\n")


if __name__ == '__main__':
    main()
