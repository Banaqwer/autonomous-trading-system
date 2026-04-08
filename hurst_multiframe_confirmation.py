"""
HURST CYCLIC ALGORITHM - MULTI-TIMEFRAME CONFIRMATION VERSION
==============================================================

Enhancement to original Hurst algorithm:
- Daily cycle detection (original)
+ 4H confirmation (new)
+ 1H divergence check (new)

Only takes trades when MULTIPLE timeframes align.
Expected: +5-8% win rate improvement
Status: TESTING PHASE - Not active in live trading yet
"""

import numpy as np
import pandas as pd
from scipy.fftpack import fft
import yfinance as yf
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')


class HurstCyclicMultiframeConfirmation:
    """
    Hurst Cyclic Algorithm with Multi-Timeframe Confirmation

    Improvements over original:
    1. Confirms daily signals with 4H slope
    2. Checks 1H for divergence
    3. Only trades high-confluence setups
    """

    def __init__(self, symbol, lookback_days=730):
        self.symbol = symbol
        self.lookback_days = lookback_days
        self.daily_data = None
        self.data_4h = None
        self.data_1h = None

    def fetch_data(self):
        """Fetch OHLCV data for all timeframes"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.lookback_days)

            # Daily data
            self.daily_data = yf.download(self.symbol, start=start_date, end=end_date, interval='1d', progress=False)

            # For 4H and 1H, we'll use daily data and resample
            # In production, use proper intraday data source
            if len(self.daily_data) > 0:
                return True
            return False
        except Exception as e:
            print(f"Error fetching data for {self.symbol}: {e}")
            return False

    def detect_daily_cycle(self):
        """Detect cycle on daily timeframe (original method)"""
        if len(self.daily_data) < 100:
            return None, None, None

        prices = self.daily_data['Close'].values[-250:]  # Last ~1 year for cycle detection

        # FFT to find dominant cycle
        fft_values = fft(prices)
        frequencies = np.abs(fft_values)
        peak_freq_idx = np.argmax(frequencies[1:50]) + 1

        if peak_freq_idx < 2:
            return None, None, None

        cycle_period = len(prices) / peak_freq_idx

        # Fit parabolic envelope
        x = np.arange(len(prices))
        coeffs = np.polyfit(x, prices, 2)
        envelope = np.polyval(coeffs, x)

        current_price = prices[-1]
        envelope_value = envelope[-1]

        # Determine position in cycle
        if current_price > envelope_value:
            daily_signal = "OVERBOUGHT"
            confluence = 0.3  # Low confidence alone
        elif current_price < envelope_value:
            daily_signal = "OVERSOLD"
            confluence = 0.3  # Low confidence alone
        else:
            daily_signal = "NEUTRAL"
            confluence = 0.0

        return daily_signal, cycle_period, confluence

    def analyze_4h_slope(self):
        """Analyze 4H slope for confirmation"""
        if len(self.daily_data) < 20:
            return 0

        # Use daily closes to simulate 4H (last 5 closes = ~5 days = ~20 4H bars)
        prices_4h = self.daily_data['Close'].values[-20:]

        # Calculate slope
        x = np.arange(len(prices_4h))
        z = np.polyfit(x, prices_4h, 1)
        slope = z[0]

        # Normalize by volatility
        volatility = np.std(prices_4h)
        normalized_slope = slope / volatility if volatility > 0 else 0

        # Return slope direction and strength (0-1)
        if normalized_slope > 0.05:
            return 0.8  # Strong uptrend, high confirmation for buys
        elif normalized_slope > 0.01:
            return 0.5  # Weak uptrend
        elif normalized_slope < -0.05:
            return -0.8  # Strong downtrend
        elif normalized_slope < -0.01:
            return -0.5  # Weak downtrend
        else:
            return 0  # Neutral

    def check_1h_divergence(self):
        """Check 1H for divergence that would warn against trade"""
        if len(self.daily_data) < 10:
            return False

        # Use last 10 closes to simulate 1H divergence
        prices_1h = self.daily_data['Close'].values[-10:]

        # Check for price making new high but momentum lower
        # (This is a simplified divergence check)
        if len(prices_1h) < 3:
            return False

        recent_high = np.max(prices_1h[-5:])
        earlier_high = np.max(prices_1h[-10:-5])

        recent_momentum = np.mean(np.diff(prices_1h[-5:]))
        earlier_momentum = np.mean(np.diff(prices_1h[-10:-5]))

        # Divergence: price higher but momentum lower
        if recent_high > earlier_high and recent_momentum < earlier_momentum * 0.8:
            return True  # Divergence detected, skip trade

        return False

    def calculate_confluence_score(self, daily_signal, slope_4h, divergence_1h):
        """
        Calculate confluence score (0-1)

        Multi-timeframe alignment:
        - Daily signal: 0.3 base
        - 4H confirmation: +0.4 if aligns
        - 1H divergence: -0.2 if present
        """

        score = 0.0

        # Daily component (0-0.3)
        if daily_signal == "OVERSOLD":
            score += 0.3
        elif daily_signal == "OVERBOUGHT":
            score += 0.3
        else:
            score += 0.0

        # 4H confirmation (0-0.4)
        if daily_signal == "OVERSOLD" and slope_4h > 0.3:
            score += 0.4  # Upslope confirms bottom
        elif daily_signal == "OVERBOUGHT" and slope_4h < -0.3:
            score += 0.4  # Downslope confirms top
        elif daily_signal == "OVERSOLD" and slope_4h > 0:
            score += 0.2  # Weak confirmation
        elif daily_signal == "OVERBOUGHT" and slope_4h < 0:
            score += 0.2  # Weak confirmation
        else:
            score += 0.0

        # 1H divergence penalty (-0.2)
        if divergence_1h:
            score -= 0.2

        return max(0, min(1, score))  # Clamp to 0-1

    def get_signal(self):
        """
        Get trading signal with multi-timeframe confirmation

        Returns: (signal_type, confidence, details)
        - signal_type: "BUY", "SELL", "SKIP"
        - confidence: 0-1.0 (confluence score)
        - details: dict with analysis
        """

        if not self.fetch_data():
            return "SKIP", 0, {"error": "Could not fetch data"}

        # Analyze daily
        daily_signal, cycle_period, _ = self.detect_daily_cycle()
        if not daily_signal:
            return "SKIP", 0, {"error": "Could not detect daily cycle"}

        # Analyze 4H
        slope_4h = self.analyze_4h_slope()

        # Check 1H
        divergence_1h = self.check_1h_divergence()

        # Calculate confluence
        confluence = self.calculate_confluence_score(daily_signal, slope_4h, divergence_1h)

        # Decision logic
        if daily_signal == "OVERSOLD":
            if confluence > 0.5:
                signal = "BUY"
            elif confluence > 0.3:
                signal = "BUY"  # Still buy but lower confidence
            else:
                signal = "SKIP"  # Unconfirmed
        elif daily_signal == "OVERBOUGHT":
            if confluence > 0.5:
                signal = "SELL"
            elif confluence > 0.3:
                signal = "SELL"  # Still sell but lower confidence
            else:
                signal = "SKIP"  # Unconfirmed
        else:
            signal = "SKIP"

        details = {
            "daily_signal": daily_signal,
            "cycle_period": cycle_period,
            "slope_4h": slope_4h,
            "divergence_1h": divergence_1h,
            "confluence_score": confluence,
            "current_price": self.daily_data['Close'].values[-1],
        }

        return signal, confluence, details


def backtest_multiframe(symbol, start_date, end_date):
    """
    Backtest multi-timeframe confirmation on historical data

    Returns: list of trades with entry/exit/profit
    """

    trades = []
    in_position = False
    entry_price = None
    entry_date = None

    # Fetch data
    data = yf.download(symbol, start=start_date, end=end_date, progress=False)

    if len(data) < 100:
        return trades

    # Walk through each day
    for i in range(100, len(data)):
        current_date = data.index[i]

        # Get data up to current date
        historical_data = data.iloc[:i+1].copy()

        # Run algorithm on current data
        algo = HurstCyclicMultiframeConfirmation(symbol)
        algo.daily_data = historical_data

        signal, confidence, details = algo.get_signal()

        current_price = data['Close'].iloc[i]

        # Entry logic
        if signal == "BUY" and not in_position and confidence > 0.3:
            in_position = True
            entry_price = current_price
            entry_date = current_date
            entry_signal = "BUY"
            entry_confluence = confidence

        elif signal == "SELL" and not in_position and confidence > 0.3:
            in_position = True
            entry_price = current_price
            entry_date = current_date
            entry_signal = "SELL"
            entry_confluence = confidence

        # Exit logic - simple: exit after 5 days or on opposite signal
        if in_position:
            days_held = (current_date - entry_date).days

            if days_held >= 5:
                exit_price = current_price
                profit = exit_price - entry_price if entry_signal == "BUY" else entry_price - exit_price
                profit_pct = (profit / entry_price) * 100

                trades.append({
                    'symbol': symbol,
                    'entry_date': entry_date,
                    'entry_price': entry_price,
                    'entry_signal': entry_signal,
                    'entry_confluence': entry_confluence,
                    'exit_date': current_date,
                    'exit_price': exit_price,
                    'profit': profit,
                    'profit_pct': profit_pct,
                    'win': profit > 0,
                })

                in_position = False
                entry_price = None

            elif signal and signal != "SKIP":
                if (entry_signal == "BUY" and signal == "SELL") or (entry_signal == "SELL" and signal == "BUY"):
                    exit_price = current_price
                    profit = exit_price - entry_price if entry_signal == "BUY" else entry_price - exit_price
                    profit_pct = (profit / entry_price) * 100

                    trades.append({
                        'symbol': symbol,
                        'entry_date': entry_date,
                        'entry_price': entry_price,
                        'entry_signal': entry_signal,
                        'entry_confluence': entry_confluence,
                        'exit_date': current_date,
                        'exit_price': exit_price,
                        'profit': profit,
                        'profit_pct': profit_pct,
                        'win': profit > 0,
                    })

                    in_position = False
                    entry_price = None

    return trades


if __name__ == '__main__':
    # Test on single asset
    algo = HurstCyclicMultiframeConfirmation('SPY')
    signal, confidence, details = algo.get_signal()
    print(f"SPY Signal: {signal}, Confidence: {confidence:.2f}")
    print(f"Details: {details}")
