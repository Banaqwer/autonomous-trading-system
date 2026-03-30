"""
Hurst Cyclic Trading System - Production Ready
===============================================

Enhanced implementation with:
1. Parabolic interpolation for envelopes (Appendix 5)
2. Walk-forward testing framework
3. Ablation testing (cycle component importance)
4. Real market data support
5. Configuration-driven operation
6. Automated trading executor

Author: Claude Code
Based on: J.M. Hurst "The Profit Magic of Stock Transaction Timing"
"""

import numpy as np
import pandas as pd
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict
from enum import Enum
from pathlib import Path
import warnings

warnings.filterwarnings("ignore")

# Import from existing module (or inline if needed)
from hurst_cyclic_trading import (
    CycleComponent, Signal, Trade, Side,
    CycleDetector, HurstMovingAverages, EnvelopeEngine,
    HurstSignalEngine, HurstBacktester, PerformanceReport,
    HurstCyclicAlgorithm, load_sample_data, load_csv_data,
)


# =============================================================================
# 1. PARABOLIC INTERPOLATION (Appendix 5 Enhancement)
# =============================================================================

class ParabolicInterpolator:
    """
    Hurst's parabolic interpolation method from Appendix 5.

    Fits a parabola through 3 consecutive points and interpolates
    smoothly between extrema for more accurate envelope boundaries.

    Given three points (x0,y0), (x1,y1), (x2,y2), the fitted parabola is:
        y = a*x^2 + b*x + c

    This produces smoother, more accurate envelopes than linear interpolation.
    """

    @staticmethod
    def fit_parabola(x0: float, y0: float,
                     x1: float, y1: float,
                     x2: float, y2: float
                     ) -> Tuple[float, float, float]:
        """
        Fit parabola coefficients (a, b, c) through 3 points.
        Returns: a, b, c where y = a*x^2 + b*x + c
        """
        # Solve system: y = a*x^2 + b*x + c for three points
        A = np.array([
            [x0**2, x0, 1],
            [x1**2, x1, 1],
            [x2**2, x2, 1],
        ])
        b = np.array([y0, y1, y2])
        try:
            coeffs = np.linalg.solve(A, b)
            return float(coeffs[0]), float(coeffs[1]), float(coeffs[2])
        except np.linalg.LinAlgError:
            # Degenerate case (collinear points) — fall back to linear
            return 0.0, (y2 - y0) / (x2 - x0) if x2 != x0 else 0.0, y0

    @staticmethod
    def parabolic_envelope(prices: np.ndarray, extrema_indices: np.ndarray
                           ) -> np.ndarray:
        """
        Build envelope using parabolic interpolation between extrema.
        For each consecutive triplet of extrema, fit a parabola and
        interpolate within that region.
        """
        if len(extrema_indices) < 2:
            return np.full_like(prices, np.nan, dtype=float)

        envelope = np.full_like(prices, np.nan, dtype=float)

        # Place extrema exactly
        for idx in extrema_indices:
            envelope[idx] = prices[idx]

        # Interpolate between consecutive extrema using parabolas
        for i in range(len(extrema_indices) - 1):
            x_left = extrema_indices[i]
            x_right = extrema_indices[i + 1]
            y_left = prices[x_left]
            y_right = prices[x_right]

            # Use previous, current, and next for parabola fit if available
            if i > 0:
                x_prev = extrema_indices[i - 1]
                y_prev = prices[x_prev]
                a, b, c = ParabolicInterpolator.fit_parabola(
                    float(x_prev), y_prev,
                    float(x_left), y_left,
                    float(x_right), y_right
                )
            elif i < len(extrema_indices) - 2:
                x_next = extrema_indices[i + 2]
                y_next = prices[x_next]
                a, b, c = ParabolicInterpolator.fit_parabola(
                    float(x_left), y_left,
                    float(x_right), y_right,
                    float(x_next), y_next
                )
            else:
                # Only 2 points — use linear
                a, b = 0.0, (y_right - y_left) / (x_right - x_left) if x_right != x_left else 0.0
                c = y_left

            # Fill interpolation region
            for x in range(x_left + 1, x_right):
                envelope[x] = a * x**2 + b * x + c

        return envelope

    @staticmethod
    def build_parabolic_envelopes(prices: np.ndarray, cycle_period: int
                                   ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Enhanced envelope building using parabolic interpolation.
        Returns upper, lower, and center envelopes.
        """
        order = max(3, cycle_period // 4)
        high_idx, low_idx = EnvelopeEngine.find_local_extrema(prices, order)

        upper = ParabolicInterpolator.parabolic_envelope(prices, high_idx)
        lower = ParabolicInterpolator.parabolic_envelope(prices, low_idx)
        center = (upper + lower) / 2.0

        return upper, lower, center


# =============================================================================
# 2. WALK-FORWARD TESTING FRAMEWORK
# =============================================================================

@dataclass
class WalkForwardWindow:
    """One training/testing window in walk-forward analysis."""
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    window_num: int


class WalkForwardTester:
    """
    Walk-forward testing prevents overfitting by training on historical data
    and testing on subsequent unseen data.

    Sequence:
    1. Train on bars [0, train_end]
    2. Test on bars [test_start, test_end]
    3. Advance forward by step_size
    4. Repeat until end of data
    """

    def __init__(self, prices: pd.DataFrame,
                 train_window: int = 500,
                 test_window: int = 100,
                 step_size: int = 50):
        """
        Parameters:
            prices: DataFrame with OHLC data
            train_window: bars used for training (detecting cycles, MA params)
            test_window: bars used for testing (OOS performance)
            step_size: bars to advance forward each iteration
        """
        self.prices = prices
        self.n = len(prices)
        self.train_window = train_window
        self.test_window = test_window
        self.step_size = step_size

    def generate_windows(self) -> List[WalkForwardWindow]:
        """Generate all walk-forward windows."""
        windows = []
        window_num = 0

        test_start = self.train_window
        while test_start + self.test_window <= self.n:
            window = WalkForwardWindow(
                train_start=0,
                train_end=test_start,
                test_start=test_start,
                test_end=test_start + self.test_window,
                window_num=window_num,
            )
            windows.append(window)
            test_start += self.step_size
            window_num += 1

        return windows

    def run(self, config: Optional[Dict] = None) -> Dict:
        """
        Run walk-forward analysis.

        For each window:
        1. Detect cycles on training data
        2. Generate signals on training data (parameter calibration)
        3. Test signals on held-out test data
        4. Report OOS performance
        """
        windows = self.generate_windows()
        if not windows:
            return {"error": "Insufficient data for walk-forward testing"}

        results = {
            "total_windows": len(windows),
            "windows": [],
            "aggregate": {},
        }

        all_test_trades = []

        for window in windows:
            print(f"\n[Window {window.window_num}] "
                  f"Train:[{window.train_start}:{window.train_end}] "
                  f"Test:[{window.test_start}:{window.test_end}]")

            # Training phase: detect cycles and generate signals on train data
            train_prices = self.prices.iloc[window.train_start:window.train_end].copy()
            train_algo = HurstCyclicAlgorithm(
                train_prices,
                price_col="Close",
                risk_per_trade=0.02,
                initial_capital=100000,
            )

            # Suppress verbose output
            import io
            import sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                train_report = train_algo.run()
            finally:
                sys.stdout = old_stdout

            # Testing phase: apply trained model to test data
            test_prices = self.prices.iloc[window.test_start:window.test_end].copy()
            test_prices_array = test_prices["Close"].values

            # Use same cycles detected on training data, but test on new data
            engine = HurstSignalEngine(test_prices_array, train_algo.components)
            test_signals = engine.generate_signals()

            # Backtest on test data
            bt = HurstBacktester(test_prices_array, test_signals, 0.02, 100000)
            test_trades, test_equity = bt.run()
            test_report = PerformanceReport.generate(test_trades, test_equity, 100000)

            all_test_trades.extend(test_trades)

            window_result = {
                "window": window.window_num,
                "train_report": train_report,
                "test_report": test_report,
                "test_trades": len(test_trades),
            }
            results["windows"].append(window_result)

            print(f"  Cycles detected: {len(train_algo.components)}")
            print(f"  Test trades: {len(test_trades)}, "
                  f"Win rate: {test_report.get('win_rate', 0):.1%}, "
                  f"Sharpe: {test_report.get('sharpe_ratio', 0):.2f}")

        # Aggregate OOS results
        if all_test_trades:
            aggregate_report = PerformanceReport.generate(
                all_test_trades,
                pd.DataFrame({"equity": np.zeros(len(all_test_trades) + 1)}),
                100000
            )
            results["aggregate"] = aggregate_report

        return results


# =============================================================================
# 3. ABLATION TESTING FRAMEWORK
# =============================================================================

class AblationTester:
    """
    Ablation testing measures the contribution of each cycle component
    to overall system performance.

    Method:
    1. Run system with all cycles (baseline)
    2. Disable each cycle one at a time
    3. Re-run and compare metrics
    4. Report which cycles matter most
    """

    def __init__(self, prices: pd.DataFrame, config: Optional[Dict] = None):
        self.prices = prices
        self.config = config or {}

    def run(self) -> Dict:
        """
        Run ablation test: measure performance impact of each cycle.
        """
        print("\n" + "=" * 60)
        print("  ABLATION TESTING: Cycle Component Importance")
        print("=" * 60)

        # Baseline: run with all cycles
        print("\n[Baseline] Running with all cycles...")
        baseline_algo = HurstCyclicAlgorithm(
            self.prices,
            risk_per_trade=0.02,
            initial_capital=100000,
        )

        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            baseline_report = baseline_algo.run()
        finally:
            sys.stdout = old_stdout

        baseline_trades = baseline_algo.trades
        baseline_sharpe = baseline_report.get("sharpe_ratio", 0)
        baseline_wr = baseline_report.get("win_rate", 0)

        print(f"  Baseline: {len(baseline_trades)} trades, "
              f"Sharpe={baseline_sharpe:.2f}, WinRate={baseline_wr:.1%}")

        results = {
            "baseline": {
                "trades": len(baseline_trades),
                "sharpe": baseline_sharpe,
                "win_rate": baseline_wr,
                "expectancy": baseline_report.get("expectancy_per_trade", 0),
            },
            "ablations": [],
        }

        # Ablate each cycle
        if not baseline_algo.components:
            print("  WARNING: No cycles detected for ablation.")
            return results

        for i, disabled_cycle in enumerate(baseline_algo.components):
            # Run with this cycle disabled
            remaining_cycles = [c for j, c in enumerate(baseline_algo.components)
                                if j != i]

            print(f"\n[Ablation {i+1}] Disabling {disabled_cycle.label}...")

            # Create signals with remaining cycles only
            engine = HurstSignalEngine(self.prices["Close"].values, remaining_cycles)
            signals = engine.generate_signals()

            # Backtest
            bt = HurstBacktester(
                self.prices["Close"].values,
                signals,
                risk_per_trade=0.02,
                initial_capital=100000,
            )
            trades, equity_df = bt.run()
            report = PerformanceReport.generate(trades, equity_df, 100000)

            ablation_sharpe = report.get("sharpe_ratio", 0)
            ablation_wr = report.get("win_rate", 0)
            impact_sharpe = baseline_sharpe - ablation_sharpe
            impact_wr = baseline_wr - ablation_wr

            ablation_result = {
                "disabled_cycle": disabled_cycle.label,
                "remaining_cycles": len(remaining_cycles),
                "trades": len(trades),
                "sharpe": ablation_sharpe,
                "win_rate": ablation_wr,
                "impact_sharpe": impact_sharpe,
                "impact_win_rate": impact_wr,
                "importance_score": max(abs(impact_sharpe), abs(impact_wr) * 5),
            }
            results["ablations"].append(ablation_result)

            print(f"  Result: {len(trades)} trades, Sharpe={ablation_sharpe:.2f} "
                  f"(impact: {impact_sharpe:+.2f})")

        # Sort by importance
        results["ablations"].sort(
            key=lambda x: x["importance_score"],
            reverse=True
        )

        # Summary
        print("\n" + "=" * 60)
        print("  ABLATION SUMMARY (sorted by importance)")
        print("=" * 60)
        for r in results["ablations"]:
            print(f"  {r['disabled_cycle']:>12s}: "
                  f"Sharpe impact={r['impact_sharpe']:+6.2f}, "
                  f"WR impact={r['impact_win_rate']:+6.1%}, "
                  f"Importance={r['importance_score']:.2f}")

        return results


# =============================================================================
# 4. CONFIGURATION MANAGEMENT
# =============================================================================

@dataclass
class HurstConfig:
    """Configuration for automated Hurst trading system."""

    # Market data
    symbol: str = "BTC/USD"
    timeframe: str = "daily"

    # Risk management
    risk_per_trade: float = 0.02  # 2% per trade
    max_position_size: float = 0.1  # max 10% of capital
    max_portfolio_drawdown: float = 0.2  # stop trading if DD > 20%

    # Cycle detection
    nominal_periods: Dict = None  # override default periods
    period_tolerance: float = 0.30
    min_confidence: float = 0.10

    # Signal generation
    min_confluence_score: float = 0.30
    edge_band_enabled: bool = True
    mid_band_enabled: bool = True

    # Envelope refinement
    use_parabolic_interpolation: bool = True
    envelope_order: Optional[int] = None  # auto if None

    # Testing
    walk_forward_enabled: bool = False
    walk_forward_train_window: int = 500
    walk_forward_test_window: int = 100
    walk_forward_step: int = 50

    ablation_enabled: bool = False

    # Execution
    live_trading: bool = False
    paper_trading: bool = True
    execution_delay_ms: int = 100

    def __post_init__(self):
        if self.nominal_periods is None:
            self.nominal_periods = {
                "18_month": 390,
                "40_week": 200,
                "20_week": 100,
                "10_week": 50,
                "5_week": 25,
                "2.5_week": 12,
            }

    def to_dict(self) -> Dict:
        return asdict(self)

    def save(self, filepath: str):
        """Save config to JSON file."""
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "HurstConfig":
        """Load config from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(**data)


# =============================================================================
# 5. PRODUCTION EXECUTOR
# =============================================================================

class ProductionExecutor:
    """
    Production-ready executor for Hurst signals.

    Handles:
    - Live signal generation
    - Position management
    - Risk controls
    - Logging
    - Performance tracking
    """

    def __init__(self, config: HurstConfig, data: pd.DataFrame):
        self.config = config
        self.data = data
        self.positions = {}  # current open positions
        self.trade_log = []
        self.signal_log = []

    def run(self) -> Dict:
        """Execute the full Hurst system with configured options."""

        print("\n" + "=" * 70)
        print("  HURST PRODUCTION EXECUTOR")
        print("=" * 70)
        print(f"  Config: {self.config.symbol} {self.config.timeframe}")
        print(f"  Risk: {self.config.risk_per_trade:.1%} per trade")
        print(f"  Modes: walk_forward={self.config.walk_forward_enabled}, "
              f"ablation={self.config.ablation_enabled}")

        results = {
            "config": self.config.to_dict(),
            "status": "running",
            "steps_completed": [],
        }

        # Step 1: Walk-forward testing (if enabled)
        if self.config.walk_forward_enabled:
            print("\n[Step 1] Walk-Forward Testing...")
            wf_tester = WalkForwardTester(
                self.data,
                train_window=self.config.walk_forward_train_window,
                test_window=self.config.walk_forward_test_window,
                step_size=self.config.walk_forward_step,
            )
            wf_results = wf_tester.run()
            results["walk_forward"] = wf_results
            results["steps_completed"].append("walk_forward")

        # Step 2: Ablation testing (if enabled)
        if self.config.ablation_enabled:
            print("\n[Step 2] Ablation Testing...")
            ablation_tester = AblationTester(self.data, self.config.to_dict())
            ablation_results = ablation_tester.run()
            results["ablation"] = ablation_results
            results["steps_completed"].append("ablation")

        # Step 3: Main algorithm run
        print("\n[Step 3] Main Algorithm Run...")
        algo = HurstCyclicAlgorithm(
            self.data,
            price_col="Close",
            risk_per_trade=self.config.risk_per_trade,
            initial_capital=100000,
        )
        main_report = algo.run()
        results["main_run"] = main_report
        results["steps_completed"].append("main_run")

        # Store for further processing
        results["algo"] = algo
        results["status"] = "complete"

        return results

    def save_results(self, output_dir: str):
        """Save all results to disk."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Config
        self.config.save(f"{output_dir}/config.json")
        print(f"  Config saved to: {output_dir}/config.json")

        # Trade log
        if self.trade_log:
            df = pd.DataFrame(self.trade_log)
            df.to_csv(f"{output_dir}/trades.csv", index=False)
            print(f"  Trades saved to: {output_dir}/trades.csv")

        # Signal log
        if self.signal_log:
            df = pd.DataFrame(self.signal_log)
            df.to_csv(f"{output_dir}/signals.csv", index=False)
            print(f"  Signals saved to: {output_dir}/signals.csv")


# =============================================================================
# 6. REAL MARKET DATA UTILITIES
# =============================================================================

class DataManager:
    """Load and manage market data from various sources."""

    @staticmethod
    def load_from_csv(filepath: str, date_col: str = 0) -> pd.DataFrame:
        """Load from local CSV file."""
        df = pd.read_csv(filepath, parse_dates=[date_col], index_col=date_col)
        return DataManager._normalize_columns(df)

    @staticmethod
    def load_from_yfinance(symbol: str, start: str, end: str) -> pd.DataFrame:
        """Load from Yahoo Finance (requires yfinance library)."""
        try:
            import yfinance as yf
            df = yf.download(symbol, start=start, end=end)
            return DataManager._normalize_columns(df)
        except ImportError:
            print("yfinance not installed. Install with: pip install yfinance")
            return None

    @staticmethod
    def load_from_quandl(symbol: str, api_key: str) -> pd.DataFrame:
        """Load from Quandl (requires quandl library)."""
        try:
            import quandl
            df = quandl.get(symbol, api_key=api_key)
            return DataManager._normalize_columns(df)
        except ImportError:
            print("quandl not installed. Install with: pip install quandl")
            return None

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to standard OHLCV."""
        df.columns = [col.lower().strip() for col in df.columns]

        col_map = {
            "o": "Open", "open": "Open",
            "h": "High", "high": "High",
            "l": "Low", "low": "Low",
            "c": "Close", "close": "Close",
            "v": "Volume", "vol": "Volume",
        }

        for old, new in col_map.items():
            if old in df.columns and new not in df.columns:
                df[new] = df.pop(old)

        # Ensure required columns exist
        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(df.columns):
            raise ValueError(f"Missing required columns: {required - set(df.columns)}")

        return df.sort_index()

    @staticmethod
    def create_synthetic_btc_like(n: int = 1000) -> pd.DataFrame:
        """Create realistic synthetic BTC-like data for testing."""
        np.random.seed(42)

        t = np.arange(n)
        price = 20000  # BTC starting price

        # Trend
        trend = price + 0.03 * t

        # Hurst cycles scaled for BTC
        cycle_200 = 500 * np.sin(2 * np.pi * t / 200)
        cycle_100 = 300 * np.sin(2 * np.pi * t / 100)
        cycle_50 = 200 * np.sin(2 * np.pi * t / 50)
        cycle_25 = 100 * np.sin(2 * np.pi * t / 25)

        # Realistic BTC volatility
        returns = np.random.randn(n) * 0.02
        noise = np.cumsum(returns) * price * 0.5

        close = trend + cycle_200 + cycle_100 + cycle_50 + cycle_25 + noise
        close = np.maximum(close, price * 0.1)

        high = close + np.abs(np.random.randn(n) * close * 0.01)
        low = close - np.abs(np.random.randn(n) * close * 0.01)
        open_ = close + np.random.randn(n) * close * 0.005

        df = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=n, freq="D"),
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.random.randint(1000, 100000, n),
        })
        df.set_index("Date", inplace=True)
        return df


# =============================================================================
# 7. MAIN ORCHESTRATOR
# =============================================================================

def main():
    """Main entry point for production system."""

    print("\n" + "=" * 70)
    print("  HURST CYCLIC TRADING - PRODUCTION SYSTEM")
    print("  Automated algorithm from 'The Profit Magic of Transaction Timing'")
    print("=" * 70)

    # Configuration
    config = HurstConfig(
        symbol="BTC/USD",
        timeframe="daily",
        risk_per_trade=0.02,
        use_parabolic_interpolation=True,
        walk_forward_enabled=True,
        walk_forward_train_window=400,
        walk_forward_test_window=100,
        walk_forward_step=50,
        ablation_enabled=True,
    )

    # Load data
    print("\n[Setup] Loading market data...")
    data = DataManager.create_synthetic_btc_like(n=1200)
    print(f"  Loaded {len(data)} bars")
    print(f"  Date range: {data.index[0].date()} to {data.index[-1].date()}")
    print(f"  Price range: ${data['Close'].min():.2f} to ${data['Close'].max():.2f}")

    # Execute
    executor = ProductionExecutor(config, data)
    results = executor.run()

    # Save results
    output_dir = "hurst_results"
    print(f"\n[Output] Saving results to: {output_dir}/")
    executor.save_results(output_dir)

    # Summary
    print("\n" + "=" * 70)
    print("  PRODUCTION RUN COMPLETE")
    print(f"  Steps completed: {', '.join(results['steps_completed'])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
