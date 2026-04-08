"""
DIAGNOSTIC: Test Hurst Algorithm Behavior Across Different Data Windows
Purpose: Identify at what data length the algorithm stops producing reports
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

class AlgorithmDiagnostic:
    """Test algorithm behavior on progressively longer data windows"""

    def __init__(self):
        self.asset = 'USO'  # Test on single asset
        self.data = None

    def download_full_data(self):
        """Download 5 years of data"""
        print("\nDownloading 5-year data for diagnostic...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*5)

        self.data = yf.download(self.asset, start=start_date, end=end_date, progress=False)
        print(f"Downloaded {len(self.data)} bars for {self.asset}")

    def test_window_size(self, window_size, confluence_threshold=0.20):
        """Test algorithm on specific window size"""
        test_data = self.data.tail(window_size)

        try:
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            algo = HurstCyclicAlgorithm(
                test_data,
                use_fld=True,
                confluence_threshold_edge=confluence_threshold,
                confluence_threshold_mid=confluence_threshold,
                confluence_threshold_fld=confluence_threshold
            )
            algo.run()

            sys.stdout = old_stdout

            if hasattr(algo, 'report') and algo.report and 'error' not in algo.report:
                trades = algo.report.get('total_trades', 0)
                wr = algo.report.get('win_rate', 0)
                return True, trades, wr
            else:
                return False, 0, 0
        except Exception as e:
            sys.stdout = old_stdout
            return False, 0, 0

    def run_diagnostic(self):
        """Test algorithm on progressively larger windows"""
        self.download_full_data()

        print("\n" + "="*100)
        print("ALGORITHM DIAGNOSTIC: Testing Different Data Window Sizes")
        print("="*100)

        window_sizes = [
            250,    # ~1 year
            500,    # ~2 years
            750,    # ~3 years
            1000,   # ~4 years
            1254,   # 5 years
        ]

        results = []

        for window in window_sizes:
            if window > len(self.data):
                print(f"\nWindow size {window:4d} bars: DATA NOT AVAILABLE")
                continue

            print(f"\nWindow size {window:4d} bars ({window/252:.1f} years)...", end='', flush=True)
            success, trades, wr = self.test_window_size(window, confluence_threshold=0.20)

            if success:
                print(f" [OK] {trades} trades, {wr:.0%} WR")
                results.append({
                    'window': window,
                    'years': window/252,
                    'status': 'SUCCESS',
                    'trades': trades,
                    'wr': wr
                })
            else:
                print(f" [FAIL] No report generated")
                results.append({
                    'window': window,
                    'years': window/252,
                    'status': 'FAIL',
                    'trades': 0,
                    'wr': 0
                })

        # Summary
        print("\n" + "="*100)
        print("SUMMARY")
        print("="*100)

        for r in results:
            status_str = "[OK]" if r['status'] == 'SUCCESS' else "[FAIL]"
            if r['status'] == 'SUCCESS':
                print(f"{r['window']:4d} bars ({r['years']:.1f}y) {status_str} - {r['trades']} trades @ {r['wr']:.0%}")
            else:
                print(f"{r['window']:4d} bars ({r['years']:.1f}y) {status_str} - Algorithm returned no report")

        # Find breaking point
        success_results = [r for r in results if r['status'] == 'SUCCESS']
        fail_results = [r for r in results if r['status'] == 'FAIL']

        if success_results:
            max_working = max([r['window'] for r in success_results])
            print(f"\nLargest successful window: {max_working} bars (~{max_working/252:.1f} years)")

        if fail_results:
            min_failing = min([r['window'] for r in fail_results])
            print(f"Smallest failing window: {min_failing} bars (~{min_failing/252:.1f} years)")
            print(f"\nBREAKING POINT: Algorithm cannot process > {min_failing} bars on {self.asset}")

        # Test with reduced threshold on largest data
        print("\n" + "="*100)
        print("TESTING REDUCED CONFLUENCE THRESHOLDS (on 1254-bar 5-year data)")
        print("="*100)

        thresholds_to_test = [0.05, 0.10, 0.15, 0.20, 0.30]

        for threshold in thresholds_to_test:
            print(f"\nThreshold {threshold:.2f}...", end='', flush=True)
            success, trades, wr = self.test_window_size(1254, confluence_threshold=threshold)

            if success:
                print(f" [OK] {trades} trades, {wr:.0%} WR")
            else:
                print(f" [FAIL] No report generated")


if __name__ == '__main__':
    diagnostic = AlgorithmDiagnostic()
    diagnostic.run_diagnostic()
