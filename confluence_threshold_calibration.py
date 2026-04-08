"""
CONFLUENCE THRESHOLD CALIBRATION
============================================================================

Find optimal confluence threshold for 15+ signals/year with 65%+ win rate
Tests thresholds: 0.20, 0.15, 0.10, 0.05
Tests assets: SPY, IWM, QQQ, EEM, GLD (5-asset hybrid)
Period: 2023-01-01 to 2024-12-31 (proven historical window)

Strategy:
- For each threshold, test all 5 assets
- Find threshold that yields 15+/year signals per asset
- Verify win rate >= 60% (acceptable)
- Select optimal threshold and assets for Phase 1A-MT

Run: python confluence_threshold_calibration.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class ConfluenceCalibrator:
    """Calibrate Hurst system confluence threshold"""

    def __init__(self):
        self.thresholds = [0.20, 0.15, 0.10, 0.05]
        self.assets = {
            'SPY': 'S&P 500',
            'IWM': 'Russell 2000',
            'QQQ': 'Nasdaq 100',
            'EEM': 'Emerging Markets',
            'GLD': 'Gold',
        }
        self.results = []

    def test_asset_threshold(self, symbol, threshold):
        """Test single asset with specific confluence threshold"""
        try:
            # Download data
            data = yf.download(symbol, start='2023-01-01', end='2024-12-31', progress=False)
            if data is None or len(data) < 50:
                return None

            # Run algorithm with tuned confluence thresholds
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            algo = HurstCyclicAlgorithm(
                data,
                use_fld=True,
                confluence_threshold_edge=threshold,
                confluence_threshold_mid=threshold,
                confluence_threshold_fld=threshold
            )

            report = algo.run()

            sys.stdout = old_stdout

            if not hasattr(algo, 'report') or algo.report is None or 'error' in algo.report:
                return None

            num_trades = algo.report.get('total_trades', 0)
            if num_trades == 0:
                return None

            win_rate = algo.report.get('win_rate', 0)
            total_return = algo.report.get('total_return_pct', 0)

            # Calculate trades per year
            days = (pd.Timestamp('2024-12-31') - pd.Timestamp('2023-01-01')).days
            years = days / 365.25
            trades_per_year = num_trades / years

            return {
                'symbol': symbol,
                'threshold': threshold,
                'num_trades': num_trades,
                'trades_per_year': trades_per_year,
                'win_rate': win_rate,
                'total_return': total_return,
            }

        except Exception as e:
            sys.stdout = old_stdout
            return None

    def run_calibration(self):
        """Run calibration across all thresholds and assets"""

        print("\n" + "="*100)
        print("CONFLUENCE THRESHOLD CALIBRATION")
        print("Finding optimal threshold for 15+ signals/year with 65%+ win rate")
        print("="*100)

        print(f"\nTesting {len(self.assets)} assets × {len(self.thresholds)} thresholds")
        print(f"Period: 2023-01-01 to 2024-12-31 (proven historical window)")
        print("\nCalibrating...\n")

        for threshold in self.thresholds:
            print(f"\n[Threshold: {threshold}]")
            for symbol, name in self.assets.items():
                print(f"  {symbol}...", end=' ', flush=True)
                result = self.test_asset_threshold(symbol, threshold)
                if result:
                    self.results.append(result)
                    print(f"OK ({result['num_trades']:2} trades, {result['trades_per_year']:5.1f}/yr, "
                          f"{result['win_rate']:.0%} WR)")
                else:
                    print("SKIP (no signals or error)")

        self.analyze_results()

    def analyze_results(self):
        """Analyze calibration results"""

        if not self.results:
            print("\nNo results from calibration")
            return

        results_df = pd.DataFrame(self.results)

        print("\n\n" + "="*100)
        print("CALIBRATION RESULTS")
        print("="*100)

        # Analyze by threshold
        print("\n[BY THRESHOLD]")
        print("-"*100)

        for threshold in self.thresholds:
            threshold_data = results_df[results_df['threshold'] == threshold]
            if len(threshold_data) > 0:
                avg_freq = threshold_data['trades_per_year'].mean()
                avg_wr = threshold_data['win_rate'].mean()
                count = len(threshold_data)

                print(f"\nThreshold {threshold}: {count} assets tested")
                print(f"  Average frequency: {avg_freq:.1f} trades/year")
                print(f"  Average win rate:  {avg_wr:.0%}")

                # Check if meets criteria
                operational = threshold_data[
                    (threshold_data['trades_per_year'] >= 15) &
                    (threshold_data['win_rate'] >= 0.65)
                ]

                if len(operational) > 0:
                    print(f"  OPERATIONAL ASSETS: {len(operational)}")
                    for idx, row in operational.iterrows():
                        print(f"    {row['symbol']}: {row['trades_per_year']:.1f}/yr @ {row['win_rate']:.0%}")

        # Analyze by asset
        print("\n\n[BY ASSET]")
        print("-"*100)

        for symbol in self.assets.keys():
            symbol_data = results_df[results_df['symbol'] == symbol]
            if len(symbol_data) > 0:
                print(f"\n{symbol}:")
                for idx, row in symbol_data.sort_values('threshold').iterrows():
                    freq_status = "[OK]" if row['trades_per_year'] >= 15 else "[LOW]"
                    wr_status = "[OK]" if row['win_rate'] >= 0.65 else "[LOW]"
                    print(f"  Threshold {row['threshold']}: {freq_status} {row['trades_per_year']:5.1f}/yr, "
                          f"{wr_status} {row['win_rate']:.0%}")

        # Recommendations
        print("\n\n" + "="*100)
        print("RECOMMENDATIONS FOR PHASE 1A-MT")
        print("="*100)

        # Find best threshold
        threshold_results = {}
        for threshold in self.thresholds:
            threshold_data = results_df[results_df['threshold'] == threshold]
            operational = threshold_data[
                (threshold_data['trades_per_year'] >= 15) &
                (threshold_data['win_rate'] >= 0.65)
            ]
            threshold_results[threshold] = len(operational)

        best_threshold = max(threshold_results.items(), key=lambda x: x[1])

        print(f"\nOptimal Confluence Threshold: {best_threshold[0]}")
        print(f"Assets meeting criteria: {best_threshold[1]}")

        if best_threshold[1] > 0:
            optimal_data = results_df[
                (results_df['threshold'] == best_threshold[0]) &
                (results_df['trades_per_year'] >= 15) &
                (results_df['win_rate'] >= 0.65)
            ]

            print(f"\n[PHASE 1A-MT ASSETS - RECOMMENDED]")
            print(f"Deploy with threshold {best_threshold[0]}:")
            for idx, row in optimal_data.iterrows():
                print(f"  {row['symbol']:8} | {row['trades_per_year']:5.1f} signals/yr | "
                      f"{row['win_rate']:.0%} WR | Return: {row['total_return']:+.2f}%")

            avg_freq = optimal_data['trades_per_year'].mean()
            avg_daily = avg_freq / 252
            print(f"\n  Average signal generation: {avg_daily:.2f} signals/day across all assets")
            print(f"  Phase 1A duration: 4-6 weeks for 15-20 signals per asset")

            print(f"\n[NEXT STEPS]")
            print(f"1. Update HurstCyclicAlgorithm to accept confluence_threshold parameter")
            print(f"2. Deploy Phase 1A-MT with threshold={best_threshold[0]}")
            print(f"3. Test on selected assets: {', '.join(optimal_data['symbol'].tolist())}")
            print(f"4. Launch Week 1 paper trading")

        else:
            print(f"\nNo threshold achieved 15+/year + 65%+ WR on tested assets")
            print(f"Best attempt:")
            best_freq = results_df.sort_values('trades_per_year', ascending=False).head(1)
            if len(best_freq) > 0:
                row = best_freq.iloc[0]
                print(f"  {row['symbol']} @ threshold {row['threshold']}: "
                      f"{row['trades_per_year']:.1f}/yr, {row['win_rate']:.0%} WR")

        print("\n" + "="*100 + "\n")


def main():
    calibrator = ConfluenceCalibrator()
    calibrator.run_calibration()


if __name__ == '__main__':
    main()
