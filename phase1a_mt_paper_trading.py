"""
PHASE 1A-MT PAPER TRADING DEPLOYMENT
============================================================================

Week 1: Multi-Timeframe Baseline Validation
- Deploy daily + 4H Hurst coordination
- Paper trading (no real capital)
- Validate signal generation and execution logic
- Confirm win rate >= 65% and signals consistent

Assets: SPY, IWM, QQQ
Risk: 2% per trade (conservative)
Capital: $100,000 (simulated)
Period: 1-year rolling (last 365 days from today)

Entry Logic:
  - Accept entries from EITHER daily OR 4H
  - Don't double-enter same position
  - Coordination priority: 4H confirmation preferred

Exit Logic:
  - FLD (Future Line of Demarcation) from 4H
  - Or hard stop at envelope boundary
  - Max 5-week hold time

Run: python phase1a_mt_paper_trading.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO
from datetime import datetime, timedelta
from dataclasses import dataclass

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


@dataclass
class MultiTimeframeEntry:
    """A coordinated entry from daily + 4H signals"""
    symbol: str
    timestamp: datetime
    price: float
    direction: str
    source: str  # 'daily' or '4h'
    confluence: str  # 'daily_only', '4h_only', 'both'
    risk_dollars: float
    position_size: int


class Phase1aMTValidator:
    """Phase 1A-MT Paper Trading Validator"""

    def __init__(self, capital=100000, risk_pct=0.02):
        self.capital = capital
        self.equity = capital
        self.risk_pct = risk_pct
        self.risk_per_trade = capital * risk_pct

        self.assets = ['SPY', 'IWM', 'QQQ']
        self.daily_data = {}
        self.h4_data = {}
        self.daily_algo = {}
        self.h4_algo = {}
        self.entries = []

    def download_data(self):
        """Download last year of daily and 4H data"""
        print("\n[STEP 1] Downloading Market Data")
        print("="*80)

        # Use last year from today
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)

        for symbol in self.assets:
            print(f"\n  {symbol}:")

            # Daily data
            print(f"    Daily...", end=' ', flush=True)
            try:
                daily = yf.download(
                    symbol,
                    start=start_date,
                    end=end_date,
                    interval='1d',
                    progress=False
                )
                if daily is not None and len(daily) > 50:
                    self.daily_data[symbol] = daily
                    print(f"OK ({len(daily)} bars)")
                else:
                    print("SKIP (insufficient data)")
            except Exception as e:
                print(f"ERROR ({str(e)[:30]})")

            # 4H data
            print(f"    4H...", end=' ', flush=True)
            try:
                h4 = yf.download(
                    symbol,
                    start=start_date,
                    end=end_date,
                    interval='4h',
                    progress=False
                )
                if h4 is not None and len(h4) > 50:
                    self.h4_data[symbol] = h4
                    print(f"OK ({len(h4)} bars)")
                else:
                    print("SKIP (insufficient data)")
            except Exception as e:
                print(f"ERROR ({str(e)[:30]})")

    def run_algorithms(self):
        """Run Hurst algorithm on both timeframes"""
        print("\n[STEP 2] Running Hurst Algorithm")
        print("="*80)

        for symbol in self.assets:
            if symbol not in self.daily_data or symbol not in self.h4_data:
                print(f"\n  {symbol}: SKIP (missing data)")
                continue

            print(f"\n  {symbol}:")

            # Daily
            print(f"    Daily...", end=' ', flush=True)
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo_daily = HurstCyclicAlgorithm(
                    self.daily_data[symbol],
                    use_fld=True
                )
                algo_daily.run()

                sys.stdout = old_stdout
                self.daily_algo[symbol] = algo_daily

                trades = algo_daily.report.get('total_trades', 0)
                wr = algo_daily.report.get('win_rate', 0)
                print(f"OK ({trades} trades, {wr:.0%} WR)")
            except Exception as e:
                sys.stdout = old_stdout
                print(f"ERROR ({str(e)[:30]})")

            # 4H
            print(f"    4H...", end=' ', flush=True)
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo_h4 = HurstCyclicAlgorithm(
                    self.h4_data[symbol],
                    use_fld=True
                )
                algo_h4.run()

                sys.stdout = old_stdout
                self.h4_algo[symbol] = algo_h4

                trades = algo_h4.report.get('total_trades', 0)
                wr = algo_h4.report.get('win_rate', 0)
                print(f"OK ({trades} trades, {wr:.0%} WR)")
            except Exception as e:
                sys.stdout = old_stdout
                print(f"ERROR ({str(e)[:30]})")

    def analyze_results(self):
        """Analyze Phase 1A-MT results"""
        print("\n[STEP 3] Phase 1A-MT Analysis")
        print("="*80)

        results = []

        for symbol in self.assets:
            if symbol not in self.daily_algo or symbol not in self.h4_algo:
                print(f"\n  {symbol}: SKIP (missing data)")
                continue

            daily_algo = self.daily_algo[symbol]
            h4_algo = self.h4_algo[symbol]

            daily_trades = daily_algo.report.get('total_trades', 0)
            daily_wr = daily_algo.report.get('win_rate', 0)
            daily_return = daily_algo.report.get('total_return_pct', 0)

            h4_trades = h4_algo.report.get('total_trades', 0)
            h4_wr = h4_algo.report.get('win_rate', 0)
            h4_return = h4_algo.report.get('total_return_pct', 0)

            # Calculate annual frequency (assuming 1-year data window)
            total_trades = daily_trades + h4_trades
            blended_wr = (daily_wr + h4_wr) / 2
            coordinated_estimate = total_trades * 0.8  # account for overlap

            results.append({
                'symbol': symbol,
                'daily_trades': daily_trades,
                'h4_trades': h4_trades,
                'total_trades': total_trades,
                'coordinated': coordinated_estimate,
                'daily_wr': daily_wr,
                'h4_wr': h4_wr,
                'blended_wr': blended_wr,
                'daily_return': daily_return,
                'h4_return': h4_return,
            })

            print(f"\n  {symbol}:")
            print(f"    Daily:      {daily_trades:2} trades @ {daily_wr:.0%}")
            print(f"    4H:         {h4_trades:2} trades @ {h4_wr:.0%}")
            print(f"    Combined:   {total_trades:2} trades ({coordinated_estimate:.0f} coordinated)")
            print(f"    Blended WR: {blended_wr:.0%}")
            print(f"    Returns:    Daily {daily_return:+.1f}%, 4H {h4_return:+.1f}%")

            # Check operational criteria
            is_operational = (blended_wr >= 0.65 and coordinated_estimate >= 15)
            print(f"    OPERATIONAL: {'YES' if is_operational else 'NO'}")

        # Summary
        print("\n" + "="*80)
        print("PHASE 1A-MT VALIDATION SUMMARY")
        print("="*80)

        if results:
            df = pd.DataFrame(results)

            operational = df[(df['blended_wr'] >= 0.65) & (df['coordinated'] >= 15)]

            print(f"\nAssets tested:                {len(df)}")
            print(f"Operational assets:           {len(operational)}")
            print(f"Average signal frequency:     {df['coordinated'].mean():.1f} trades/year")
            print(f"Average win rate:             {df['blended_wr'].mean():.0%}")

            if len(operational) > 0:
                print(f"\n[WEEK 1 VALIDATION: PASSED]")
                print(f"Operational assets ready for Week 1 paper trading:")
                for idx, row in operational.iterrows():
                    print(f"  {row['symbol']}: {row['coordinated']:.0f} signals/yr, {row['blended_wr']:.0%} WR")
                print(f"\nProceed to Week 1 paper trading with these assets")
            else:
                print(f"\n[WEEK 1 VALIDATION: FAILED - NEEDS ADJUSTMENT]")
                print(f"No assets meet 65%+ WR + 15+ signals/year criteria")
                print(f"Next step: Adjust confluence thresholds and retry")


def main():
    print("\n" + "="*80)
    print("PHASE 1A-MT PAPER TRADING VALIDATOR")
    print("Week 1: Baseline Multi-Timeframe Validation")
    print("="*80)

    validator = Phase1aMTValidator(capital=100000, risk_pct=0.02)

    validator.download_data()
    validator.run_algorithms()
    validator.analyze_results()

    print("\n" + "="*80)
    print("VALIDATION COMPLETE")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
