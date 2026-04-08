"""
SIMPLIFIED BACKTEST COMPARISON
===============================

Tests original vs multi-timeframe on subset of reliable assets
Handles download failures gracefully
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
from hurst_multiframe_confirmation import HurstCyclicMultiframeConfirmation
import warnings
from scipy.fftpack import fft

warnings.filterwarnings('ignore')


class SimpleBacktestComparison:
    """Simple, robust backtest comparison"""

    def __init__(self):
        # Use only most reliable assets (avoid penny stocks, delisted)
        self.assets = ['SPY', 'QQQ', 'IWM', 'EEM', 'VTI', 'VOE', 'DGRO', 'SCHD',
                       'VGRO', 'VOOV', 'VUG', 'VTV', 'VBR', 'VBK']  # 14 assets

        self.original_all_trades = []
        self.multiframe_all_trades = []

    def original_signal(self, data):
        """Get signal from original Hurst algorithm"""
        if len(data) < 100:
            return "SKIP"

        prices = data['Close'].values[-250:] if len(data) >= 250 else data['Close'].values

        if len(prices) < 50:
            return "SKIP"

        try:
            fft_values = fft(prices)
            frequencies = np.abs(fft_values)
            peak_freq_idx = np.argmax(frequencies[1:50]) + 1

            if peak_freq_idx >= 2:
                x = np.arange(len(prices))
                coeffs = np.polyfit(x, prices, 2)
                envelope = np.polyval(coeffs, x)

                current_price = prices[-1]
                envelope_value = envelope[-1]

                if current_price > envelope_value * 1.02:
                    return "SELL"
                elif current_price < envelope_value * 0.98:
                    return "BUY"
        except:
            pass

        return "SKIP"

    def multiframe_signal(self, data):
        """Get signal from multi-timeframe algorithm"""
        if len(data) < 100:
            return "SKIP", 0.0

        try:
            # Directly analyze without fetching (data already provided)
            if len(data) < 100:
                return "SKIP", 0.0

            prices = data['Close'].values[-250:] if len(data) >= 250 else data['Close'].values

            if len(prices) < 50:
                return "SKIP", 0.0

            # FFT to find dominant cycle (same as original)
            fft_values = fft(prices)
            frequencies = np.abs(fft_values)
            peak_freq_idx = np.argmax(frequencies[1:50]) + 1

            if peak_freq_idx < 2:
                return "SKIP", 0.0

            # Daily signal
            x = np.arange(len(prices))
            coeffs = np.polyfit(x, prices, 2)
            envelope = np.polyval(coeffs, x)

            current_price = prices[-1]
            envelope_value = envelope[-1]

            if current_price > envelope_value:
                daily_signal = "OVERBOUGHT"
            elif current_price < envelope_value:
                daily_signal = "OVERSOLD"
            else:
                daily_signal = "NEUTRAL"

            # 4H slope confirmation (last 20 closes = ~5 days = ~20 4H bars)
            prices_4h = data['Close'].values[-20:]
            x = np.arange(len(prices_4h))
            z = np.polyfit(x, prices_4h, 1)
            slope = z[0]
            volatility = np.std(prices_4h)
            normalized_slope = slope / volatility if volatility > 0 else 0

            # 1H divergence check
            prices_1h = data['Close'].values[-10:]
            if len(prices_1h) >= 3:
                recent_high = np.max(prices_1h[-5:])
                earlier_high = np.max(prices_1h[-10:-5])
                recent_momentum = np.mean(np.diff(prices_1h[-5:]))
                earlier_momentum = np.mean(np.diff(prices_1h[-10:-5]))
                divergence_1h = recent_high > earlier_high and recent_momentum < earlier_momentum * 0.8
            else:
                divergence_1h = False

            # Confluence score
            score = 0.0
            if daily_signal in ["OVERBOUGHT", "OVERSOLD"]:
                score += 0.3
            if daily_signal == "OVERSOLD" and normalized_slope > 0.3:
                score += 0.4
            elif daily_signal == "OVERBOUGHT" and normalized_slope < -0.3:
                score += 0.4
            elif daily_signal == "OVERSOLD" and normalized_slope > 0:
                score += 0.2
            elif daily_signal == "OVERBOUGHT" and normalized_slope < 0:
                score += 0.2

            if divergence_1h:
                score -= 0.2

            confidence = max(0, min(1, score))

            # Decision logic
            if daily_signal == "OVERSOLD" and confidence > 0.3:
                return "BUY", confidence
            elif daily_signal == "OVERBOUGHT" and confidence > 0.3:
                return "SELL", confidence
            else:
                return "SKIP", 0.0

        except:
            pass

        return "SKIP", 0.0

    def backtest_asset(self, symbol):
        """Backtest both algorithms on single asset"""
        try:
            # Download 3 years of data (faster than 5 years)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=1095)

            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if data is None or len(data) < 100:
                print(f"  {symbol}: Skipped (insufficient data)")
                return

            original_trades = []
            multiframe_trades = []
            in_position = False
            entry_price = None
            entry_date = None
            entry_type = None
            orig_signal_to_log = None
            multi_signal_to_log = None
            multi_confidence = 0

            for i in range(100, len(data)):
                current_date = data.index[i]
                historical_data = data.iloc[:i+1].copy()
                current_price = data['Close'].iloc[i]

                # Original algorithm
                if not in_position:
                    orig_signal = self.original_signal(historical_data)
                    if orig_signal != "SKIP":
                        in_position = True
                        entry_price = current_price
                        entry_date = current_date
                        entry_type = "original"
                        orig_signal_to_log = orig_signal

                # Multi-timeframe
                if not in_position:
                    multi_signal, confidence = self.multiframe_signal(historical_data)
                    if multi_signal != "SKIP":
                        in_position = True
                        entry_price = current_price
                        entry_date = current_date
                        entry_type = "multiframe"
                        multi_signal_to_log = multi_signal
                        multi_confidence = confidence

                # Exit logic
                if in_position:
                    days_held = (current_date - entry_date).days

                    if days_held >= 5:
                        exit_price = current_price

                        if entry_type == "original" and orig_signal_to_log:
                            profit = exit_price - entry_price if orig_signal_to_log == "BUY" else entry_price - exit_price
                            pct_return = (profit / entry_price * 100) if entry_price > 0 else 0
                            original_trades.append({
                                'profit': profit,
                                'win': profit > 0,
                                'pct': pct_return
                            })
                        elif entry_type == "multiframe" and multi_signal_to_log:
                            profit = exit_price - entry_price if multi_signal_to_log == "BUY" else entry_price - exit_price
                            pct_return = (profit / entry_price * 100) if entry_price > 0 else 0
                            multiframe_trades.append({
                                'profit': profit,
                                'win': profit > 0,
                                'pct': pct_return,
                                'confidence': multi_confidence
                            })

                        in_position = False
                        orig_signal_to_log = None
                        multi_signal_to_log = None

            self.original_all_trades.extend(original_trades)
            self.multiframe_all_trades.extend(multiframe_trades)

            print(f"  {symbol}: Original={len(original_trades)} trades, Multi={len(multiframe_trades)} trades")

        except Exception as e:
            print(f"  {symbol}: Error - {str(e)[:50]}")

    def analyze(self, trades):
        """Analyze trades"""
        if not trades:
            return {
                'total': 0,
                'wins': 0,
                'win_rate': 0,
                'avg_profit': 0,
                'sharpe': 0,
                'total_profit': 0,
            }

        total = len(trades)
        wins = 0
        total_profit = 0
        profits = []
        returns = []

        for trade in trades:
            try:
                profit = float(trade.get('profit', 0))
                pct = float(trade.get('pct', 0))
                win = bool(trade.get('win', False))

                total_profit += profit
                profits.append(profit)
                returns.append(pct)

                if win:
                    wins += 1
            except:
                pass

        win_rate = (wins / total * 100) if total > 0 else 0
        avg_profit = sum(profits) / len(profits) if len(profits) > 0 else 0

        # Sharpe ratio
        if len(returns) > 1:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            std_dev = variance ** 0.5
            sharpe = (mean_ret / std_dev) if std_dev > 0 else 0
        else:
            sharpe = 0

        return {
            'total': total,
            'wins': wins,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'sharpe': sharpe,
            'total_profit': total_profit,
        }

    def run(self):
        """Run comparison backtest"""
        print("\n" + "="*80)
        print("BACKTEST COMPARISON: ORIGINAL vs MULTI-TIMEFRAME CONFIRMATION")
        print("="*80)
        print("\nTesting on 14 reliable assets, 3-year period...\n")

        for symbol in self.assets:
            self.backtest_asset(symbol)

        # Analyze results
        print("\n" + "="*80)
        print("RESULTS")
        print("="*80)

        orig_stats = self.analyze(self.original_all_trades)
        multi_stats = self.analyze(self.multiframe_all_trades)

        print(f"\nORIGINAL ALGORITHM:")
        print(f"  Total Trades: {orig_stats['total']}")
        print(f"  Wins: {orig_stats['wins']} | Win Rate: {orig_stats['win_rate']:.1f}%")
        print(f"  Avg Profit: ${orig_stats['avg_profit']:.2f}")
        print(f"  Total Profit: ${orig_stats['total_profit']:.2f}")
        print(f"  Sharpe Ratio: {orig_stats['sharpe']:.2f}")

        print(f"\nMULTI-TIMEFRAME CONFIRMATION:")
        print(f"  Total Trades: {multi_stats['total']}")
        print(f"  Wins: {multi_stats['wins']} | Win Rate: {multi_stats['win_rate']:.1f}%")
        print(f"  Avg Profit: ${multi_stats['avg_profit']:.2f}")
        print(f"  Total Profit: ${multi_stats['total_profit']:.2f}")
        print(f"  Sharpe Ratio: {multi_stats['sharpe']:.2f}")

        print(f"\nCOMPARISON:")
        wr_diff = multi_stats['win_rate'] - orig_stats['win_rate']
        profit_diff = multi_stats['total_profit'] - orig_stats['total_profit']
        sharpe_diff = multi_stats['sharpe'] - orig_stats['sharpe']

        print(f"  Win Rate: {orig_stats['win_rate']:.1f}% → {multi_stats['win_rate']:.1f}% ({wr_diff:+.1f}%)")
        print(f"  Total Profit: ${orig_stats['total_profit']:.2f} → ${multi_stats['total_profit']:.2f} ({profit_diff:+.2f})")
        print(f"  Sharpe: {orig_stats['sharpe']:.2f} → {multi_stats['sharpe']:.2f} ({sharpe_diff:+.2f})")

        print(f"\nVERDICT:")
        if wr_diff > 3:
            print(f"  ✓ UPGRADE RECOMMENDED - Multi-timeframe shows {wr_diff:.1f}% improvement")
        elif wr_diff > 0:
            print(f"  ~ MARGINAL - Small {wr_diff:.1f}% improvement")
        else:
            print(f"  ✗ NO IMPROVEMENT - Original is better")

        print("\n" + "="*80)


if __name__ == '__main__':
    backtest = SimpleBacktestComparison()
    backtest.run()
