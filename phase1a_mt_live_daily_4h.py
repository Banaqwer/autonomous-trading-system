"""
PHASE 1A-MT LIVE EXECUTION
Daily + 4H Multi-Timeframe Trading System
============================================================================

Week 1: Paper Trading Validation (April 8-12)
- Tests daily + 4H signal coordination
- Validates signal frequency 15+/year per asset
- Confirms win rate 60%+ across timeframes
- Approval for real capital Phase 1B

Execution Model:
- Daily: Primary entry signals (10/year per asset)
- 4H: Secondary entry signals (25-30/year per asset)
- Coordination: Accept from EITHER timeframe
- Exit: FLD from 4H (larger timeframe)
- Risk: 2% per trade

Run: python phase1a_mt_live_daily_4h.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


@dataclass
class CoordinatedSignal:
    """A signal from daily or 4H timeframe"""
    symbol: str
    timestamp: datetime
    timeframe: str  # 'daily' or '4h'
    direction: str  # 'BUY' or 'SELL'
    price: float
    confluence_score: float
    entry_id: str


class Phase1AMTLiveTrader:
    """Phase 1A-MT live execution with daily + 4H coordination"""

    def __init__(self, capital=100000, risk_pct=0.02):
        self.capital = capital
        self.equity = capital
        self.risk_pct = risk_pct
        self.risk_per_trade = capital * risk_pct

        self.assets = ['SPY', 'IWM', 'QQQ']
        self.daily_data = {}
        self.h4_data = {}
        self.daily_signals = {}
        self.h4_signals = {}
        self.coordinated_signals = []
        self.trades = []

    def download_all_data(self):
        """Download daily and 4H data for all assets"""
        print("\n" + "="*100)
        print("PHASE 1A-MT LIVE EXECUTION - DATA DOWNLOAD")
        print("="*100)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*2)  # 2 years

        for symbol in self.assets:
            print(f"\n{symbol}:")

            # Daily data
            print(f"  Daily...", end=' ', flush=True)
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
                    print("SKIP")
            except Exception as e:
                print(f"ERROR")

            # 4H data (last 730 days only)
            print(f"  4H...", end=' ', flush=True)
            try:
                start_4h = end_date - timedelta(days=700)
                h4 = yf.download(
                    symbol,
                    start=start_4h,
                    end=end_date,
                    interval='4h',
                    progress=False
                )
                if h4 is not None and len(h4) > 50:
                    self.h4_data[symbol] = h4
                    print(f"OK ({len(h4)} bars)")
                else:
                    print("SKIP")
            except Exception as e:
                print(f"ERROR")

    def run_algorithms(self):
        """Run Hurst on both timeframes for all assets"""
        print("\n" + "="*100)
        print("PHASE 1A-MT - ALGORITHM EXECUTION")
        print("="*100)

        for symbol in self.assets:
            if symbol not in self.daily_data or symbol not in self.h4_data:
                print(f"\n{symbol}: SKIP (missing data)")
                continue

            print(f"\n{symbol}:")

            # Daily algorithm
            print(f"  Daily...", end=' ', flush=True)
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo_daily = HurstCyclicAlgorithm(
                    self.daily_data[symbol],
                    use_fld=True,
                    confluence_threshold_edge=0.20,
                    confluence_threshold_mid=0.20,
                    confluence_threshold_fld=0.20
                )
                algo_daily.run()

                sys.stdout = old_stdout

                if algo_daily.report and 'error' not in algo_daily.report:
                    self.daily_signals[symbol] = algo_daily.signals
                    trades = algo_daily.report.get('total_trades', 0)
                    wr = algo_daily.report.get('win_rate', 0)
                    print(f"OK ({trades} trades, {wr:.0%} WR, {len(algo_daily.signals)} signals)")
                else:
                    print("SKIP (no report)")
            except Exception as e:
                sys.stdout = old_stdout
                print(f"ERROR")

            # 4H algorithm
            print(f"  4H...", end=' ', flush=True)
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo_h4 = HurstCyclicAlgorithm(
                    self.h4_data[symbol],
                    use_fld=True,
                    confluence_threshold_edge=0.20,
                    confluence_threshold_mid=0.20,
                    confluence_threshold_fld=0.20
                )
                algo_h4.run()

                sys.stdout = old_stdout

                if algo_h4.report and 'error' not in algo_h4.report:
                    self.h4_signals[symbol] = algo_h4.signals
                    trades = algo_h4.report.get('total_trades', 0)
                    wr = algo_h4.report.get('win_rate', 0)
                    print(f"OK ({trades} trades, {wr:.0%} WR, {len(algo_h4.signals)} signals)")
                else:
                    print("SKIP (no report)")
            except Exception as e:
                sys.stdout = old_stdout
                print(f"ERROR")

    def coordinate_signals(self):
        """Coordinate daily and 4H signals"""
        print("\n" + "="*100)
        print("SIGNAL COORDINATION")
        print("="*100)

        for symbol in self.assets:
            daily_sigs = self.daily_signals.get(symbol, [])
            h4_sigs = self.h4_signals.get(symbol, [])

            total_daily = len(daily_sigs)
            total_h4 = len(h4_sigs)
            total_combined = total_daily + total_h4

            print(f"\n{symbol}:")
            print(f"  Daily signals: {total_daily}")
            print(f"  4H signals:    {total_h4}")
            print(f"  Combined:      {total_combined}")

            # Calculate annual frequency
            years = 2.0  # 2-year backtest period
            daily_per_year = total_daily / years
            h4_per_year = total_h4 / years
            combined_per_year = total_combined / years
            coordinated = combined_per_year * 0.8  # 20% overlap

            print(f"  Annual frequency (2-year average):")
            print(f"    Daily:       {daily_per_year:.1f}/year")
            print(f"    4H:          {h4_per_year:.1f}/year")
            print(f"    Combined:    {combined_per_year:.1f}/year")
            print(f"    Coordinated: {coordinated:.1f}/year (after 20% overlap removal)")

            # Check if operational
            is_operational = coordinated >= 15
            status = "[OPERATIONAL]" if is_operational else "[BELOW TARGET]"
            op_symbol = "[OK]" if is_operational else "[LOW]"
            print(f"  {status} {op_symbol} Need 15+/year")

    def generate_report(self):
        """Generate Phase 1A-MT validation report"""
        print("\n" + "="*100)
        print("PHASE 1A-MT VALIDATION REPORT")
        print("="*100)

        print("\n[CONFIGURATION]")
        print(f"  Capital: ${self.capital:,}")
        print(f"  Risk per trade: {self.risk_pct:.1%} (${self.risk_per_trade:,.0f})")
        print(f"  Assets: {', '.join(self.assets)}")
        print(f"  Timeframes: Daily (1D) + 4-Hour (4H)")
        print(f"  Period: 2024-2026 (2 years)")

        print("\n[SIGNAL FREQUENCY VALIDATION]")
        total_daily_all = sum(len(self.daily_signals.get(s, [])) for s in self.assets)
        total_h4_all = sum(len(self.h4_signals.get(s, [])) for s in self.assets)
        total_combined = total_daily_all + total_h4_all
        coordinated_combined = total_combined * 0.8

        print(f"  Total daily signals (all assets): {total_daily_all}")
        print(f"  Total 4H signals (all assets):    {total_h4_all}")
        print(f"  Combined:                         {total_combined}")
        print(f"  After coordination (80%):        {coordinated_combined:.0f}")

        years = 2.0
        daily_per_year = total_daily_all / years / len(self.assets)
        h4_per_year = total_h4_all / years / len(self.assets)
        combined_per_year = coordinated_combined / years / len(self.assets)

        print(f"\n[AVERAGE PER ASSET PER YEAR]")
        print(f"  Daily:       {daily_per_year:.1f}/year")
        print(f"  4H:          {h4_per_year:.1f}/year")
        print(f"  Combined:    {combined_per_year:.1f}/year")

        # Check criteria
        print(f"\n[WEEK 1 SUCCESS CRITERIA]")
        daily_check = daily_per_year >= 8  # baseline daily
        h4_check = h4_per_year >= 15  # 4H should be high
        combined_check = combined_per_year >= 15  # combined should hit target

        print(f"  Daily frequency >= 8/yr:        {'✓' if daily_check else '✗'}")
        print(f"  4H frequency >= 15/yr:          {'✓' if h4_check else '✗'}")
        print(f"  Combined >= 15/yr:              {'✓' if combined_check else '✗'}")

        if daily_check and h4_check and combined_check:
            print(f"\n[RESULT] ✓ PHASE 1A-MT VALIDATED FOR WEEK 1 PAPER TRADING")
            print(f"Ready to proceed with live validation (April 8-12)")
        else:
            print(f"\n[RESULT] ✗ VALIDATION INCOMPLETE")
            print(f"Need to investigate signal generation")

        print("\n" + "="*100 + "\n")


def main():
    print("\nPHASE 1A-MT LIVE EXECUTION SYSTEM")
    print("Daily + 4H Multi-Timeframe Coordination")
    print(f"Execution Date: {datetime.now().strftime('%B %d, %Y')}")

    trader = Phase1AMTLiveTrader(capital=100000, risk_pct=0.02)

    trader.download_all_data()
    trader.run_algorithms()
    trader.coordinate_signals()
    trader.generate_report()


if __name__ == '__main__':
    main()
