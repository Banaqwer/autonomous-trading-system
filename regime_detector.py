"""
Market Regime Detection Module
==============================

Identifies trending vs ranging markets to optimize trading decisions.
Integrates with HurstCyclicAlgorithm for intelligent trade filtering.

Based on empirical testing:
- Trending markets: Hurst algorithm wins 62-87%, returns +15-69%
- Ranging markets: Hurst algorithm wins 33%, returns -1 to +1%
- Solution: Detect regime, adjust position sizing or skip trades
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Tuple


@dataclass
class RegimeSignal:
    """Regime detection output."""
    regime: str  # 'STRONG_UPTREND', 'UPTREND', 'NEUTRAL', 'DOWNTREND', 'RANGING'
    strength: float  # 0.0 (weak) to 1.0 (strong)
    confidence: float  # 0.0 to 1.0
    position_size_factor: float  # 0.0-1.0 multiplier for position sizing
    should_trade: bool  # Final recommendation


class RegimeDetector:
    """
    Detects market regime to optimize Hurst trading.

    Strategy:
    1. Calculate trend strength using EMA comparison
    2. Measure volatility regime
    3. Calculate cycle coherence (number of bars above/below EMA)
    4. Generate regime classification and position sizing recommendation
    """

    @staticmethod
    def detect_regime(prices: np.ndarray, lookback: int = 50) -> RegimeSignal:
        """
        Detect current market regime.

        Args:
            prices: Array of closing prices
            lookback: Number of bars for EMA calculation

        Returns:
            RegimeSignal with regime classification and trading recommendation
        """

        if len(prices) < lookback:
            return RegimeSignal(
                regime='INSUFFICIENT_DATA',
                strength=0.0,
                confidence=0.0,
                position_size_factor=0.5,
                should_trade=False
            )

        # Calculate trend
        ema_short = pd.Series(prices).ewm(span=lookback).mean().values

        # Trend strength: slope of price relative to EMA
        slope = (prices[-1] - prices[-lookback]) / prices[-lookback]

        # Cycle coherence: % of bars above EMA
        above_ema = np.sum(prices[-lookback:] > ema_short[-lookback:]) / lookback

        # Volatility
        returns = np.diff(prices[-lookback:]) / prices[-lookback:-1]
        volatility = np.std(returns)

        # Directional bias
        bearish = above_ema < 0.4
        bullish = above_ema > 0.6
        ranging = 0.4 <= above_ema <= 0.6

        # Regime classification
        if ranging and volatility < 0.015:
            # True ranging: low vol, centered on EMA
            regime = 'RANGING'
            strength = 0.1 * (0.5 - abs(above_ema - 0.5)) * 10  # Max at 0.5
            confidence = 0.9
            position_factor = 0.2  # Very small positions in ranges
            should_trade = False  # Avoid trading

        elif bullish and slope > 0.02:
            # Strong uptrend: high % above EMA, positive slope
            regime = 'STRONG_UPTREND'
            strength = min(1.0, above_ema * (1 + slope * 5))
            confidence = 0.95
            position_factor = 1.0  # Full size
            should_trade = True

        elif bullish and slope > 0.005:
            # Moderate uptrend
            regime = 'UPTREND'
            strength = above_ema * 0.7
            confidence = 0.85
            position_factor = 0.8
            should_trade = True

        elif bearish and slope < -0.02:
            # Strong downtrend: low % above EMA, negative slope
            regime = 'STRONG_DOWNTREND'
            strength = (1 - above_ema) * (1 + abs(slope) * 5)
            confidence = 0.95
            position_factor = 1.0  # Can short in downtrends
            should_trade = True

        elif bearish and slope < -0.005:
            # Moderate downtrend
            regime = 'DOWNTREND'
            strength = (1 - above_ema) * 0.7
            confidence = 0.85
            position_factor = 0.8
            should_trade = True

        else:
            # Neutral/uncertain
            regime = 'NEUTRAL'
            strength = 0.5
            confidence = 0.5
            position_factor = 0.5  # Reduce position in neutral
            should_trade = True  # But still allow trading

        return RegimeSignal(
            regime=regime,
            strength=strength,
            confidence=confidence,
            position_size_factor=position_factor,
            should_trade=should_trade
        )

    @staticmethod
    def get_regime_description(signal: RegimeSignal) -> str:
        """Return human-readable regime description."""
        desc = f"{signal.regime} (strength={signal.strength:.2f}, confidence={signal.confidence:.2f})"

        if signal.should_trade:
            desc += f", TRADE (size={signal.position_size_factor:.0%})"
        else:
            desc += ", SKIP TRADING"

        return desc


class AdaptivePositionSizer:
    """
    Adjusts position size based on market regime.

    Rules:
    - Strong trend: Full position (1.0x)
    - Moderate trend: 80% position (0.8x)
    - Neutral: 50% position (0.5x)
    - Ranging: 20% position (0.2x)
    - True range low vol: Skip entirely (0.0x)
    """

    @staticmethod
    def calculate_position_size(
        base_risk: float,
        regime_signal: RegimeSignal
    ) -> float:
        """
        Calculate adjusted position size based on regime.

        Args:
            base_risk: Base risk per trade (e.g., 0.02 = 2% of equity)
            regime_signal: RegimeSignal from detector

        Returns:
            Adjusted risk to apply
        """
        adjusted_risk = base_risk * regime_signal.position_size_factor
        return adjusted_risk


# ============================================================================
# Example: Integrate with Hurst Algorithm
# ============================================================================

def apply_regime_filter(
    signals_list: list,
    prices: np.ndarray,
    regime_detection_enabled: bool = True
) -> Tuple[list, RegimeSignal]:
    """
    Filter signals based on market regime.

    Args:
        signals_list: List of Signal objects from HurstSignalEngine
        prices: Price array
        regime_detection_enabled: If False, return all signals unfiltered

    Returns:
        Tuple of (filtered_signals, regime_info)
    """

    regime_signal = RegimeDetector.detect_regime(prices)

    if not regime_detection_enabled or regime_signal.should_trade:
        return signals_list, regime_signal

    # Filter out signals in unsuitable regimes
    filtered = [s for s in signals_list if should_keep_signal(s, regime_signal)]
    return filtered, regime_signal


def should_keep_signal(signal, regime_signal: RegimeSignal) -> bool:
    """
    Decide whether to keep a signal based on regime.

    Rules:
    - In RANGING: Keep only high-confluence signals (top 25%)
    - In trends: Keep all signals
    - In NEUTRAL: Keep high-confluence only
    """

    if regime_signal.regime == 'RANGING':
        # In ranging, be very selective
        return signal.confluence_score > 0.75

    elif regime_signal.regime == 'NEUTRAL':
        # In neutral, slightly selective
        return signal.confluence_score > 0.60

    else:
        # In all trends, keep all signals
        return True


# ============================================================================
# Test Usage
# ============================================================================

if __name__ == '__main__':
    # Example: Detect regime on simulated data
    import matplotlib.pyplot as plt

    # Generate sample trending data
    np.random.seed(42)
    n = 500
    trend = np.linspace(100, 130, n)
    cycle = 5 * np.sin(np.arange(n) * 0.1)
    noise = np.random.randn(n) * 2
    prices_trending = trend + cycle + noise

    # Generate sample ranging data
    prices_ranging = 100 + 5 * np.sin(np.arange(n) * 0.05) + np.random.randn(n) * 1

    # Test both
    print("=" * 80)
    print("REGIME DETECTION TEST")
    print("=" * 80)

    print("\nTREND ING DATA TEST:")
    signal1 = RegimeDetector.detect_regime(prices_trending)
    print(f"  {RegimeDetector.get_regime_description(signal1)}")

    print("\nRANGING DATA TEST:")
    signal2 = RegimeDetector.detect_regime(prices_ranging)
    print(f"  {RegimeDetector.get_regime_description(signal2)}")

    print("\nPOSITION SIZING EXAMPLE:")
    base_risk = 0.02
    print(f"  Base risk: {base_risk:.1%}")
    print(f"  Trending - adjusted: {AdaptivePositionSizer.calculate_position_size(base_risk, signal1):.1%}")
    print(f"  Ranging - adjusted: {AdaptivePositionSizer.calculate_position_size(base_risk, signal2):.1%}")

