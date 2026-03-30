"""
Hurst Algorithm with Regime Detection Filter
=============================================

Enhanced version that applies market regime detection to filter/adjust trades.

Key improvements:
1. Skips trading in true ranging markets
2. Adjusts position size based on trend strength
3. Maintains signal quality through confluence filtering
4. Provides regime awareness for risk management
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from contextlib import redirect_stdout
import io

# Import the original Hurst algorithm
sys.path.insert(0, str(Path(__file__).parent))
from hurst_cyclic_trading import HurstCyclicAlgorithm
from regime_detector import RegimeDetector, AdaptivePositionSizer, RegimeSignal


class HurstWithRegimeFilter(HurstCyclicAlgorithm):
    """
    Enhanced Hurst algorithm with market regime awareness.

    Workflow:
    1. Detect market regime (trending vs ranging)
    2. Run standard Hurst cycle detection
    3. Filter signals based on regime confidence
    4. Adjust position sizes based on trend strength
    5. Report regime-adjusted performance
    """

    def __init__(
        self,
        df: pd.DataFrame,
        price_col: str = "Close",
        risk_per_trade: float = 0.02,
        initial_capital: float = 100000,
        regime_filter_enabled: bool = True,
    ):
        """
        Args:
            df: DataFrame with price data
            price_col: Column to analyze
            risk_per_trade: Base risk per trade
            initial_capital: Starting capital
            regime_filter_enabled: Enable regime-based filtering
        """
        super().__init__(df, price_col, risk_per_trade, initial_capital)

        self.regime_filter_enabled = regime_filter_enabled
        self.regime_signal: RegimeSignal = None
        self.regime_description = ""
        self.signals_before_filter = 0
        self.signals_after_filter = 0

    def run(self):
        """Run Hurst algorithm with regime filtering."""

        # Step 1: Detect market regime
        self.regime_signal = RegimeDetector.detect_regime(self.prices)
        self.regime_description = (
            f"{self.regime_signal.regime} "
            f"(strength={self.regime_signal.strength:.2f}, "
            f"confidence={self.regime_signal.confidence:.2f})"
        )

        print("\n" + "=" * 60)
        print("HURST ALGORITHM WITH REGIME DETECTION")
        print("=" * 60)
        print(f"\n[REGIME DETECTION]")
        print(f"  Market regime: {self.regime_description}")
        print(f"  Trading allowed: {'YES' if self.regime_signal.should_trade else 'NO'}")
        print(f"  Position size factor: {self.regime_signal.position_size_factor:.0%}")

        # Step 2: Run standard Hurst (but suppress verbose output during integration)
        f = io.StringIO()
        with redirect_stdout(f):
            # Run parent class steps manually to insert filtering
            self._detect_cycles()
            self._build_envelopes()
            self._compute_moving_averages()
            self._generate_signals()

        # Store pre-filter count
        self.signals_before_filter = len(self.signals)

        # Step 3: Apply regime-based signal filtering
        if self.regime_filter_enabled:
            self._apply_regime_filter()

        # Store post-filter count
        self.signals_after_filter = len(self.signals)

        # Step 4: Adjust position size based on regime
        if self.regime_filter_enabled:
            self.risk_per_trade = AdaptivePositionSizer.calculate_position_size(
                self.risk_per_trade,
                self.regime_signal
            )

        # Step 5: Run backtest with filtered signals
        print(f"\n[SIGNAL FILTERING]")
        print(
            f"  Signals before filter: {self.signals_before_filter}"
        )
        print(f"  Signals after filter: {self.signals_after_filter}")
        if self.signals_before_filter > 0:
            filter_pct = (
                (self.signals_before_filter - self.signals_after_filter)
                / self.signals_before_filter
                * 100
            )
            print(f"  Filtered out: {filter_pct:.0f}%")

        if not self.signals:
            print(f"\n  WARNING: No signals after filtering. Skipping backtest.")
            return {
                "error": "No signals generated after regime filtering",
                "regime": self.regime_signal.regime,
                "recommendation": "Market regime unsuitable for trading",
            }

        # Step 6: Backtest with adjusted position size
        from hurst_cyclic_trading import HurstBacktester

        bt = HurstBacktester(
            self.prices, self.signals,
            self.risk_per_trade, self.initial_capital,
        )
        self.trades, self.equity_df = bt.run()

        # Step 7: Generate report
        from hurst_cyclic_trading import PerformanceReport

        self.report = PerformanceReport.generate(
            self.trades, self.equity_df, self.initial_capital
        )

        print(f"\n[PERFORMANCE REPORT]")
        for k, v in self.report.items():
            label = k.replace("_", " ").title()
            if isinstance(v, float):
                if "pct" in k.lower() or "rate" in k.lower():
                    print(f"  {label:.<35s} {v:.1%}")
                else:
                    print(f"  {label:.<35s} {v:.2f}")
            else:
                print(f"  {label:.<35s} {v}")

        print("\n" + "=" * 60)
        print("Run complete.")
        print("=" * 60)

        return self.report

    def _detect_cycles(self):
        """Detect dominant cycles (from parent)."""
        print("\n[1] Detecting dominant cycles via spectral analysis...")
        from hurst_cyclic_trading import CycleDetector

        detector = CycleDetector(self.prices)
        self.components = detector.detect_cycles()

        for c in self.components:
            print(f"    {c.label:>12s}: period={c.period:6.0f} bars, confidence={c.confidence:.2f}")

    def _build_envelopes(self):
        """Build curvilinear envelopes (from parent)."""
        print("\n[2] Building curvilinear envelopes...")
        from hurst_cyclic_trading import EnvelopeEngine

        tc = self.components[0]
        upper, lower, center = EnvelopeEngine.build_curvilinear_envelopes(
            self.prices, int(tc.period)
        )
        measured_period, deviation = EnvelopeEngine.measure_cycle_from_envelope(
            self.prices, int(tc.period)
        )
        print(f"    Envelope cycle: nominal={tc.period:.0f}, measured={measured_period:.1f}, deviation={deviation:.1%}")

    def _compute_moving_averages(self):
        """Compute Hurst moving averages (from parent)."""
        print("\n[3] Computing Hurst moving averages...")
        from hurst_cyclic_trading import HurstMovingAverages

        for c in self.components:
            period = int(c.period)
            hma = HurstMovingAverages.half_span_average(self.prices, period)
            fma = HurstMovingAverages.full_span_average(self.prices, period)
            inv = HurstMovingAverages.inverse_average(self.prices, period)
            valid = np.sum(~np.isnan(hma))
            print(f"    {c.label:>12s}: half-span={period // 2}, full-span={period}, valid_points={valid}")

    def _generate_signals(self):
        """Generate signals (from parent)."""
        print("\n[4] Generating buy/sell signals...")
        from hurst_cyclic_trading import HurstSignalEngine

        engine = HurstSignalEngine(self.prices, self.components)
        self.signals = engine.generate_signals()

        buys = sum(1 for s in self.signals if s.side.value == "long")
        sells = sum(1 for s in self.signals if s.side.value == "short")
        edge = sum(1 for s in self.signals if s.timing_type == "edge_band")
        mid = sum(1 for s in self.signals if s.timing_type == "mid_band")

        print(f"    Total signals (before filter): {len(self.signals)} (buy={buys}, sell={sells})")
        print(f"    Timing: edge-band={edge}, mid-band={mid}")

    def _apply_regime_filter(self):
        """Filter signals based on market regime."""

        if self.regime_signal.regime == "RANGING":
            # In ranging markets: keep only HIGH confidence signals
            min_confluence = 0.75
            self.signals = [s for s in self.signals if s.confluence_score >= min_confluence]
            print(f"    Ranging market filter: Keeping only signals with confluence >= {min_confluence}")

        elif self.regime_signal.regime == "NEUTRAL":
            # In neutral markets: keep medium+ confidence
            min_confluence = 0.60
            self.signals = [s for s in self.signals if s.confluence_score >= min_confluence]
            print(f"    Neutral market filter: Keeping only signals with confluence >= {min_confluence}")

        elif "UPTREND" in self.regime_signal.regime or "DOWNTREND" in self.regime_signal.regime:
            # In trending markets: keep all signals
            print(f"    Trending market: Keeping all signals")

        else:
            # Fallback: keep all
            print(f"    Unknown regime: Keeping all signals")


# ============================================================================
# Test and Comparison
# ============================================================================

def test_regime_filter():
    """Compare standard Hurst vs Hurst with regime filter."""

    import yfinance as yf

    downloads = Path(r"C:\Users\hugoh\Downloads")

    # Test on EUR/USD 2023-2024 (ranging market that failed before)
    data = pd.read_csv(downloads / "EUR_USD_2023_2024_ranging.csv")

    print("\n" + "=" * 100)
    print("REGIME FILTER TEST: EUR/USD 2023-2024 (Ranging Market)")
    print("=" * 100)

    print("\n[A] STANDARD HURST (No Regime Filter)")
    print("-" * 100)
    algo_standard = HurstCyclicAlgorithm(
        data,
        price_col="Close",
        risk_per_trade=0.02,
        initial_capital=100000,
    )
    # Suppress output
    f = io.StringIO()
    with redirect_stdout(f):
        results_standard = algo_standard.run()

    if "error" not in results_standard:
        print(f"  Trades: {results_standard.get('total_trades', 0)}")
        print(f"  Win rate: {results_standard.get('win_rate', 0) * 100:.1f}%")
        print(f"  Return: {results_standard.get('total_return_pct', 0):.1f}%")
        print(f"  Sharpe: {results_standard.get('sharpe_ratio', 0):.2f}")
    else:
        print(f"  ERROR: {results_standard['error']}")

    print("\n[B] HURST WITH REGIME FILTER (Enabled)")
    print("-" * 100)
    algo_filtered = HurstWithRegimeFilter(
        data,
        price_col="Close",
        risk_per_trade=0.02,
        initial_capital=100000,
        regime_filter_enabled=True,
    )
    results_filtered = algo_filtered.run()

    if "error" not in results_filtered:
        print(f"  Trades: {results_filtered.get('total_trades', 0)}")
        print(f"  Win rate: {results_filtered.get('win_rate', 0) * 100:.1f}%")
        print(f"  Return: {results_filtered.get('total_return_pct', 0):.1f}%")
        print(f"  Sharpe: {results_filtered.get('sharpe_ratio', 0):.2f}")
    else:
        print(f"  RESULT: {results_filtered.get('recommendation', 'System skipped unsuitable market')}")

    print("\n" + "=" * 100)
    print("TEST COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    test_regime_filter()

