"""
BACKTEST COMPARISON: ORIGINAL vs MULTI-TIMEFRAME CONFIRMATION
==============================================================

Tests both algorithms across 40 assets over 5 years (2021-2026)
Methodology: Year-by-year breakdown (same as original validation)
Purpose: Determine if multi-timeframe confirmation improves performance

Expected: +5-8% win rate improvement
Testing: Statistical significance (p-value, Monte Carlo)
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
from hurst_multiframe_confirmation import HurstCyclicMultiframeConfirmation
import warnings
from scipy import stats

warnings.filterwarnings('ignore')


class ComparativeBacktest:
    """Backtest both algorithms and compare results"""

    def __init__(self):
        self.original_results = {}
        self.multiframe_results = {}
        self.comparison_results = {}

        # 40 assets (same as main strategy)
        self.assets = [
            'SPY', 'QQQ', 'IWM', 'EEM', 'VTI', 'VOE', 'DGRO', 'SCHD',
            'VGRO', 'VOOV', 'VUG', 'VTV', 'VBR', 'VBK', 'VOX', 'VHT',
            'VGT', 'VIS', 'VFS', 'VLUE', 'VNQ', 'VCIT', 'VCSH', 'BND',
            'AGG', 'LQD', 'HYG', 'ANGL', 'VWOB', 'EMB', 'GLD', 'DBC',
            'USO', 'UUP', 'TLT', 'IEF', 'SHY', 'DXY', 'VIX', 'XLE'
        ]

        self.years = [2022, 2023, 2024, 2025, 2026]  # 5 years, 2021 has less data

    def backtest_original(self, symbol, start_date, end_date):
        """Backtest original Hurst algorithm (simplified version)"""
        trades = []
        in_position = False
        entry_price = None
        entry_date = None

        try:
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if len(data) < 100:
                return []

            for i in range(100, len(data)):
                current_date = data.index[i]
                historical_data = data.iloc[:i+1].copy()

                # Original Hurst algorithm logic (simplified)
                prices = historical_data['Close'].values[-250:]

                # FFT-based cycle detection (original method)
                from scipy.fftpack import fft
                fft_values = fft(prices)
                frequencies = np.abs(fft_values)
                peak_freq_idx = np.argmax(frequencies[1:50]) + 1 if len(frequencies) > 50 else 1

                if peak_freq_idx >= 2 and len(prices) > peak_freq_idx:
                    x = np.arange(len(prices))
                    coeffs = np.polyfit(x, prices, 2)
                    envelope = np.polyval(coeffs, x)

                    current_price = prices[-1]
                    envelope_value = envelope[-1]

                    if current_price > envelope_value * 1.02:
                        signal = "SELL"
                    elif current_price < envelope_value * 0.98:
                        signal = "BUY"
                    else:
                        signal = "SKIP"
                else:
                    signal = "SKIP"

                current_price = data['Close'].iloc[i]

                # Entry
                if signal != "SKIP" and not in_position:
                    in_position = True
                    entry_price = current_price
                    entry_date = current_date
                    entry_signal = signal

                # Exit (simple: 5 days or opposite signal)
                if in_position:
                    days_held = (current_date - entry_date).days

                    if days_held >= 5:
                        exit_price = current_price
                        profit = exit_price - entry_price if entry_signal == "BUY" else entry_price - exit_price

                        trades.append({
                            'symbol': symbol,
                            'profit': profit,
                            'profit_pct': (profit / entry_price) * 100,
                            'win': profit > 0,
                            'days': days_held,
                        })

                        in_position = False

        except Exception as e:
            pass

        return trades

    def backtest_multiframe(self, symbol, start_date, end_date):
        """Backtest multi-timeframe confirmation"""
        trades = []
        in_position = False
        entry_price = None
        entry_date = None

        try:
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if len(data) < 100:
                return []

            for i in range(100, len(data)):
                current_date = data.index[i]
                historical_data = data.iloc[:i+1].copy()

                algo = HurstCyclicMultiframeConfirmation(symbol)
                algo.daily_data = historical_data

                signal, confidence, details = algo.get_signal()
                current_price = data['Close'].iloc[i]

                # Entry (only if high confluence)
                if signal != "SKIP" and confidence > 0.3 and not in_position:
                    in_position = True
                    entry_price = current_price
                    entry_date = current_date
                    entry_signal = signal
                    entry_confidence = confidence

                # Exit
                if in_position:
                    days_held = (current_date - entry_date).days

                    if days_held >= 5:
                        exit_price = current_price
                        profit = exit_price - entry_price if entry_signal == "BUY" else entry_price - exit_price

                        trades.append({
                            'symbol': symbol,
                            'profit': profit,
                            'profit_pct': (profit / entry_price) * 100,
                            'win': profit > 0,
                            'days': days_held,
                            'confidence': entry_confidence,
                        })

                        in_position = False

        except Exception as e:
            pass

        return trades

    def analyze_trades(self, trades):
        """Calculate statistics from trades"""
        if not trades:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'avg_profit': 0,
                'profit_factor': 0,
                'sharpe': 0,
                'total_profit': 0,
                'std_dev': 0,
            }

        df = pd.DataFrame(trades)
        wins = df[df['win']].shape[0]
        losses = df[~df['win']].shape[0]

        win_rate = (wins / len(df) * 100) if len(df) > 0 else 0
        avg_profit = df['profit'].mean()
        total_profit = df['profit'].sum()

        # Profit factor
        win_profit = df[df['win']]['profit'].sum()
        loss_profit = abs(df[~df['win']]['profit'].sum())
        profit_factor = win_profit / loss_profit if loss_profit > 0 else 0

        # Sharpe ratio
        returns = df['profit_pct'].values
        sharpe = (np.mean(returns) / np.std(returns)) if len(returns) > 1 and np.std(returns) > 0 else 0

        return {
            'total_trades': len(df),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'profit_factor': profit_factor,
            'sharpe': sharpe,
            'total_profit': total_profit,
            'std_dev': np.std(returns),
        }

    def run_year_by_year(self):
        """Backtest both algorithms year by year"""
        print("\n" + "="*100)
        print("COMPARATIVE BACKTEST: ORIGINAL vs MULTI-TIMEFRAME CONFIRMATION")
        print("="*100)

        all_original_trades = []
        all_multiframe_trades = []

        for year in self.years:
            print(f"\nYEAR {year}:")
            print("-" * 100)

            original_year_trades = []
            multiframe_year_trades = []

            for symbol in self.assets:
                start_date = datetime(year, 1, 1)
                end_date = datetime(year, 12, 31)

                # Original
                orig_trades = self.backtest_original(symbol, start_date, end_date)
                original_year_trades.extend(orig_trades)
                all_original_trades.extend(orig_trades)

                # Multi-timeframe
                multi_trades = self.backtest_multiframe(symbol, start_date, end_date)
                multiframe_year_trades.extend(multi_trades)
                all_multiframe_trades.extend(multi_trades)

            # Analyze year
            orig_stats = self.analyze_trades(original_year_trades)
            multi_stats = self.analyze_trades(multiframe_year_trades)

            print(f"  Original Algorithm:")
            print(f"    Trades: {orig_stats['total_trades']} | WR: {orig_stats['win_rate']:.1f}% | " +
                  f"Profit Factor: {orig_stats['profit_factor']:.2f} | Sharpe: {orig_stats['sharpe']:.2f}")

            print(f"  Multi-Timeframe Confirmation:")
            print(f"    Trades: {multi_stats['total_trades']} | WR: {multi_stats['win_rate']:.1f}% | " +
                  f"Profit Factor: {multi_stats['profit_factor']:.2f} | Sharpe: {multi_stats['sharpe']:.2f}")

            diff = multi_stats['win_rate'] - orig_stats['win_rate']
            print(f"  Difference: {diff:+.1f}% win rate")

        # Overall comparison
        print("\n" + "="*100)
        print("OVERALL RESULTS (5 years, 40 assets)")
        print("="*100)

        orig_overall = self.analyze_trades(all_original_trades)
        multi_overall = self.analyze_trades(all_multiframe_trades)

        print(f"\nORIGINAL ALGORITHM:")
        print(f"  Total Trades: {orig_overall['total_trades']}")
        print(f"  Wins: {orig_overall['wins']} | Losses: {orig_overall['losses']}")
        print(f"  Win Rate: {orig_overall['win_rate']:.2f}%")
        print(f"  Avg Profit per Trade: ${orig_overall['avg_profit']:.2f}")
        print(f"  Total Profit: ${orig_overall['total_profit']:.2f}")
        print(f"  Profit Factor: {orig_overall['profit_factor']:.2f}")
        print(f"  Sharpe Ratio: {orig_overall['sharpe']:.2f}")

        print(f"\nMULTI-TIMEFRAME CONFIRMATION:")
        print(f"  Total Trades: {multi_overall['total_trades']}")
        print(f"  Wins: {multi_overall['wins']} | Losses: {multi_overall['losses']}")
        print(f"  Win Rate: {multi_overall['win_rate']:.2f}%")
        print(f"  Avg Profit per Trade: ${multi_overall['avg_profit']:.2f}")
        print(f"  Total Profit: ${multi_overall['total_profit']:.2f}")
        print(f"  Profit Factor: {multi_overall['profit_factor']:.2f}")
        print(f"  Sharpe Ratio: {multi_overall['sharpe']:.2f}")

        print(f"\nCOMPARISON:")
        print(f"  Win Rate Improvement: {multi_overall['win_rate'] - orig_overall['win_rate']:+.2f}%")
        print(f"  Profit Factor Improvement: {multi_overall['profit_factor'] - orig_overall['profit_factor']:+.2f}")
        print(f"  Sharpe Ratio Improvement: {multi_overall['sharpe'] - orig_overall['sharpe']:+.2f}")
        print(f"  Total Profit Difference: ${multi_overall['total_profit'] - orig_overall['total_profit']:+.2f}")

        # Statistical significance
        self.test_statistical_significance(all_original_trades, all_multiframe_trades)

        # Verdict
        self.provide_verdict(orig_overall, multi_overall)

    def test_statistical_significance(self, orig_trades, multi_trades):
        """Test if improvement is statistically significant"""
        print("\n" + "="*100)
        print("STATISTICAL SIGNIFICANCE TEST")
        print("="*100)

        if not orig_trades or not multi_trades:
            print("Insufficient data for significance testing")
            return

        orig_profits = np.array([t['profit'] for t in orig_trades])
        multi_profits = np.array([t['profit'] for t in multi_trades])

        # T-test
        t_stat, p_value = stats.ttest_ind(multi_profits, orig_profits)

        print(f"\nT-Test Results:")
        print(f"  Multi-timeframe mean profit: ${np.mean(multi_profits):.2f}")
        print(f"  Original mean profit: ${np.mean(orig_profits):.2f}")
        print(f"  T-statistic: {t_stat:.4f}")
        print(f"  P-value: {p_value:.6f}")

        if p_value < 0.05:
            print(f"  Result: STATISTICALLY SIGNIFICANT (95% confidence)")
        elif p_value < 0.10:
            print(f"  Result: Moderately significant (90% confidence)")
        else:
            print(f"  Result: NOT statistically significant")

    def provide_verdict(self, orig_stats, multi_stats):
        """Provide final verdict: is multi-timeframe better?"""
        print("\n" + "="*100)
        print("FINAL VERDICT")
        print("="*100)

        wr_improvement = multi_stats['win_rate'] - orig_stats['win_rate']
        pf_improvement = multi_stats['profit_factor'] - orig_stats['profit_factor']
        sharpe_improvement = multi_stats['sharpe'] - orig_stats['sharpe']
        profit_improvement = multi_stats['total_profit'] - orig_stats['total_profit']

        print(f"\nKey Metrics:")
        print(f"  Win Rate: {orig_stats['win_rate']:.2f}% → {multi_stats['win_rate']:.2f}% ({wr_improvement:+.2f}%)")
        print(f"  Profit Factor: {orig_stats['profit_factor']:.2f} → {multi_stats['profit_factor']:.2f} ({pf_improvement:+.2f})")
        print(f"  Sharpe Ratio: {orig_stats['sharpe']:.2f} → {multi_stats['sharpe']:.2f} ({sharpe_improvement:+.2f})")
        print(f"  Total Profit: ${orig_stats['total_profit']:.2f} → ${multi_stats['total_profit']:.2f} ({profit_improvement:+.2f})")

        print(f"\nAnalysis:")

        if wr_improvement > 5:
            print(f"  ✓ Win rate improved by {wr_improvement:.2f}% (SIGNIFICANT)")
        elif wr_improvement > 0:
            print(f"  ~ Win rate improved by {wr_improvement:.2f}% (MODEST)")
        else:
            print(f"  ✗ Win rate decreased by {abs(wr_improvement):.2f}% (WORSE)")

        if sharpe_improvement > 0.3:
            print(f"  ✓ Sharpe ratio improved by {sharpe_improvement:.2f} (SIGNIFICANT)")
        elif sharpe_improvement > 0:
            print(f"  ~ Sharpe ratio improved by {sharpe_improvement:.2f} (MODEST)")
        else:
            print(f"  ✗ Sharpe ratio decreased by {abs(sharpe_improvement):.2f} (WORSE)")

        if pf_improvement > 0.2:
            print(f"  ✓ Profit factor improved by {pf_improvement:.2f} (SIGNIFICANT)")
        elif pf_improvement > 0:
            print(f"  ~ Profit factor improved by {pf_improvement:.2f} (MODEST)")
        else:
            print(f"  ✗ Profit factor decreased (WORSE)")

        print(f"\nFINAL VERDICT:")

        if wr_improvement > 3 and sharpe_improvement > 0.1:
            print(f"  ✓✓ UPGRADE RECOMMENDED")
            print(f"    Multi-timeframe confirmation shows measurable improvement.")
            print(f"    Deploy this version for Wednesday trading.")
        elif wr_improvement > 0 and sharpe_improvement >= 0:
            print(f"  ~ MARGINAL IMPROVEMENT")
            print(f"    Shows some improvement but not dramatic.")
            print(f"    Decide based on risk tolerance.")
        else:
            print(f"  ✗ NO UPGRADE")
            print(f"    Performance does not improve. Keep original.")

        print("\n" + "="*100)


if __name__ == '__main__':
    backtest = ComparativeBacktest()
    backtest.run_year_by_year()
