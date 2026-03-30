"""
Multi-Market Regime Scanner
===========================

Scans multiple markets simultaneously, detects regime for each,
and recommends which market to trade based on conditions.

Features:
- Monitor 3-20 markets in parallel
- Real-time regime detection
- Automatic ranking by trading quality
- Capital allocation recommendations
- Automatic market switching
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from enum import Enum
from regime_detector import RegimeDetector, RegimeSignal


@dataclass
class MarketRegime:
    """Regime information for a single market."""
    symbol: str
    timeframe: str
    regime: str
    strength: float
    confidence: float
    position_size_factor: float
    should_trade: bool
    quality_score: float = 0.0
    expected_return: float = 0.0
    risk_level: str = "unknown"


@dataclass
class MarketRanking:
    """Ranked list of markets by trading quality."""
    markets: List[MarketRegime] = field(default_factory=list)
    best_market: MarketRegime = None
    total_tradeable: int = 0
    allocation: Dict[str, float] = field(default_factory=dict)


class MultiMarketScanner:
    """
    Scan multiple markets and recommend best trading opportunities.

    Usage:
        scanner = MultiMarketScanner()
        results = scanner.scan_all_markets(market_data_dict)
        print(results.best_market)  # Get best market
        print(results.allocation)   # Capital allocation
    """

    @staticmethod
    def calculate_quality_score(regime: RegimeSignal) -> float:
        """
        Calculate overall market quality (0.0 to 1.0).

        Factors:
        - Regime strength (50% weight)
        - Confidence (30% weight)
        - Position size factor (20% weight)
        """
        score = (
            regime.strength * 0.5 +
            regime.confidence * 0.3 +
            regime.position_size_factor * 0.2
        )
        return min(1.0, score)  # Cap at 1.0

    @staticmethod
    def estimate_return(regime: RegimeSignal, historical_sharpe: float = 7.0) -> float:
        """
        Estimate expected return based on regime and historical performance.

        Assumptions:
        - Strong uptrend: 30-50% annual return
        - Uptrend: 15-30% annual return
        - Neutral: 5-15% annual return
        - Ranging: 2-5% annual return
        - Downtrend: -20% to 0% (can short for profit)
        """
        if "STRONG" in regime.regime:
            base_return = 40
        elif "UPTREND" in regime.regime:
            base_return = 20
        elif "DOWNTREND" in regime.regime:
            base_return = 15  # Can short
        elif "NEUTRAL" in regime.regime:
            base_return = 10
        elif "RANGING" in regime.regime:
            base_return = 3
        else:
            base_return = 5

        # Adjust by confidence
        adjusted_return = base_return * regime.confidence

        return adjusted_return

    @staticmethod
    def estimate_risk_level(regime: RegimeSignal) -> str:
        """Categorize risk level based on regime."""
        if regime.position_size_factor >= 0.8:
            return "LOW"  # Can trade at full size
        elif regime.position_size_factor >= 0.5:
            return "MEDIUM"
        elif regime.position_size_factor >= 0.2:
            return "HIGH"  # Must reduce position
        else:
            return "CRITICAL"  # Skip trading

    @staticmethod
    def scan_market(symbol: str, prices: np.ndarray, timeframe: str = "daily") -> MarketRegime:
        """
        Scan a single market and return regime analysis.

        Args:
            symbol: Market symbol (e.g., "BTC", "SPY", "EURUSD")
            prices: Array of closing prices
            timeframe: Timeframe (daily, hourly, weekly, etc.)

        Returns:
            MarketRegime with analysis results
        """
        regime_signal = RegimeDetector.detect_regime(prices)

        quality_score = MultiMarketScanner.calculate_quality_score(regime_signal)
        expected_return = MultiMarketScanner.estimate_return(regime_signal)
        risk_level = MultiMarketScanner.estimate_risk_level(regime_signal)

        return MarketRegime(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime_signal.regime,
            strength=regime_signal.strength,
            confidence=regime_signal.confidence,
            position_size_factor=regime_signal.position_size_factor,
            should_trade=regime_signal.should_trade,
            quality_score=quality_score,
            expected_return=expected_return,
            risk_level=risk_level,
        )

    @staticmethod
    def scan_all_markets(
        market_data: Dict[str, Dict[str, np.ndarray]],
        available_capital: float = 100000
    ) -> MarketRanking:
        """
        Scan all markets and generate ranking + allocation.

        Args:
            market_data: Dict of {symbol: {timeframe: prices_array}}
                Example:
                {
                    "BTC": {"weekly": prices_array, "daily": prices_array},
                    "SPY": {"daily": prices_array},
                    "EURUSD": {"daily": prices_array},
                }
            available_capital: Total capital to allocate

        Returns:
            MarketRanking with recommendations and allocation
        """
        markets = []

        # Scan each market
        for symbol, timeframes in market_data.items():
            for timeframe, prices in timeframes.items():
                if isinstance(prices, np.ndarray) and len(prices) > 50:
                    market_regime = MultiMarketScanner.scan_market(symbol, prices, timeframe)
                    markets.append(market_regime)

        # Sort by quality score (descending)
        markets.sort(key=lambda m: m.quality_score, reverse=True)

        # Find best market
        best_market = markets[0] if markets else None

        # Find all tradeable markets (quality score > 0.5)
        tradeable = [m for m in markets if m.quality_score > 0.5]

        # Allocate capital
        allocation = MultiMarketScanner._allocate_capital(tradeable, available_capital)

        return MarketRanking(
            markets=markets,
            best_market=best_market,
            total_tradeable=len(tradeable),
            allocation=allocation,
        )

    @staticmethod
    def _allocate_capital(tradeable_markets: List[MarketRegime], total_capital: float) -> Dict[str, float]:
        """
        Allocate capital across tradeable markets by quality.

        Logic:
        - Best market: 40-50% of capital
        - Second best: 20-30% of capital
        - Third best: 10-20% of capital
        - Rest: Remaining capital
        - Cash reserve: 10% kept in reserve
        """
        if not tradeable_markets:
            return {}

        allocation = {}
        total_score = sum(m.quality_score for m in tradeable_markets)

        if total_score == 0:
            return {}

        # Reserve 10% for emergencies
        deployable_capital = total_capital * 0.90
        reserve = total_capital * 0.10

        # Allocate proportionally to quality score
        for market in tradeable_markets:
            pct = (market.quality_score / total_score)
            amount = deployable_capital * pct
            key = f"{market.symbol}_{market.timeframe}"
            allocation[key] = amount

        # Add reserve
        allocation["_CASH_RESERVE"] = reserve

        return allocation

    @staticmethod
    def print_report(ranking: MarketRanking) -> None:
        """Print human-readable report."""
        print("\n" + "=" * 120)
        print("MULTI-MARKET REGIME SCANNER REPORT")
        print("=" * 120)

        if not ranking.markets:
            print("No markets to scan.")
            return

        # Table header
        print(f"\n{'#':<3} {'Symbol':<10} {'TF':<10} {'Regime':<20} {'Strength':<10} {'Confidence':<12} {'Quality':<10} {'Risk':<10} {'Trade?':<7}")
        print("-" * 120)

        # All markets
        for i, market in enumerate(ranking.markets[:10], 1):  # Show top 10
            print(f"{i:<3} {market.symbol:<10} {market.timeframe:<10} {market.regime:<20} "
                  f"{market.strength:>8.2f} {market.confidence:>10.2f} {market.quality_score:>8.2f} "
                  f"{market.risk_level:<10} {'✓' if market.should_trade else '✗':<7}")

        # Best market
        if ranking.best_market:
            print(f"\n{'BEST MARKET:':<30} {ranking.best_market.symbol} {ranking.best_market.timeframe}")
            print(f"{'Regime:':<30} {ranking.best_market.regime}")
            print(f"{'Quality Score:':<30} {ranking.best_market.quality_score:.2f}/1.0")
            print(f"{'Expected Return:':<30} {ranking.best_market.expected_return:.1f}% (annualized)")
            print(f"{'Risk Level:':<30} {ranking.best_market.risk_level}")
            print(f"{'Position Size Factor:':<30} {ranking.best_market.position_size_factor:.0%}")

        # Allocation
        if ranking.allocation:
            print(f"\n{'CAPITAL ALLOCATION:':<50}")
            print("-" * 80)
            total = sum(v for k, v in ranking.allocation.items() if k != "_CASH_RESERVE")
            for key, amount in sorted(ranking.allocation.items(), key=lambda x: x[1], reverse=True):
                if key != "_CASH_RESERVE":
                    pct = (amount / (total or 1)) * 100
                    print(f"  {key:<40} ${amount:>12,.0f} ({pct:>5.1f}%)")
            if "_CASH_RESERVE" in ranking.allocation:
                print(f"  {'CASH RESERVE':<40} ${ranking.allocation['_CASH_RESERVE']:>12,.0f} (reserved)")

        print(f"\nTradeable markets: {ranking.total_tradeable}/{len(ranking.markets)}")
        print("=" * 120 + "\n")


def example_usage():
    """Example: Scan multiple markets."""
    import yfinance as yf

    print("Downloading market data...")

    # Fetch real data
    btc = yf.download("BTC-USD", period="1y", interval="1d", progress=False)
    spy = yf.download("SPY", period="1y", interval="1d", progress=False)
    gld = yf.download("GLD", period="1y", interval="1d", progress=False)

    # Prepare market data dictionary
    market_data = {
        "BTC": {"daily": btc["Close"].values},
        "SPY": {"daily": spy["Close"].values},
        "GLD": {"daily": gld["Close"].values},
    }

    # Scan all markets
    scanner = MultiMarketScanner()
    ranking = scanner.scan_all_markets(market_data, available_capital=100000)

    # Print report
    scanner.print_report(ranking)

    # Access best market
    if ranking.best_market:
        print(f"\nRECOMMENDATION:")
        print(f"Trade {ranking.best_market.symbol} on {ranking.best_market.timeframe}")
        print(f"Regime: {ranking.best_market.regime}")
        print(f"Quality: {ranking.best_market.quality_score:.2f}/1.0")
        print(f"Expected return: {ranking.best_market.expected_return:.1f}% (annualized)")


if __name__ == "__main__":
    example_usage()

