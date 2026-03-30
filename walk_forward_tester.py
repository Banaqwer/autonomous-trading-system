"""
Walk-Forward Testing
====================

Out-of-sample validation: train on past data, test on future data.

Prevents overfitting by ensuring system works on data it hasn't "seen" during design.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class WalkForwardWindow:
    """Single train/test window."""
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_bars: int
    test_bars: int


@dataclass
class WalkForwardResult:
    """Results for one window."""
    window_idx: int
    train_return: float
    test_return: float
    train_sharpe: float
    test_sharpe: float
    train_wr: float
    test_wr: float
    degradation: float  # test_return - train_return


class WalkForwardTester:
    """
    Train on historical data, test on future data.

    Typical setup:
    - Train: 60-70% of data
    - Test: 30-40% of data
    - Walk forward: Slide window to test multiple periods
    """

    @staticmethod
    def create_windows(
        total_bars: int,
        train_pct: float = 0.60,
        test_pct: float = 0.40,
        step: int = None
    ) -> List[WalkForwardWindow]:
        """
        Create walk-forward windows.

        Args:
            total_bars: Total bars in dataset
            train_pct: Fraction for training (e.g., 0.60 = 60%)
            test_pct: Fraction for testing
            step: Step size for sliding window (None = no overlap)

        Returns:
            List of window definitions
        """
        if step is None:
            step = int(total_bars * test_pct)  # Non-overlapping

        train_size = int(total_bars * train_pct)
        test_size = int(total_bars * test_pct)

        windows = []
        idx = 0

        while idx + train_size + test_size <= total_bars:
            window = WalkForwardWindow(
                train_start=idx,
                train_end=idx + train_size,
                test_start=idx + train_size,
                test_end=idx + train_size + test_size,
                train_bars=train_size,
                test_bars=test_size
            )
            windows.append(window)
            idx += step

        return windows

    @staticmethod
    def analyze_degradation(results: List[WalkForwardResult]) -> dict:
        """
        Analyze how much performance degrades on out-of-sample data.

        Returns: Statistics on degradation
        """
        if not results:
            return {}

        train_returns = [r.train_return for r in results]
        test_returns = [r.test_return for r in results]
        degradations = [r.degradation for r in results]

        train_sharpes = [r.train_sharpe for r in results]
        test_sharpes = [r.test_sharpe for r in results]

        return {
            'train_return_mean': np.mean(train_returns),
            'test_return_mean': np.mean(test_returns),
            'degradation_mean': np.mean(degradations),
            'degradation_std': np.std(degradations),
            'degradation_pct': (np.mean(degradations) / np.mean(train_returns) * 100) if np.mean(
                train_returns) != 0 else 0,
            'train_sharpe_mean': np.mean(train_sharpes),
            'test_sharpe_mean': np.mean(test_sharpes),
            'sharpe_degradation': np.mean(train_sharpes) - np.mean(test_sharpes),
            'test_profitable_pct': sum(1 for r in results if r.test_return > 0) / len(results) * 100,
        }

    @staticmethod
    def calculate_metrics(prices: np.ndarray, trades_list, initial_capital: float = 100000) -> Tuple[float, float, float]:
        """
        Calculate return, Sharpe, and win rate from trades.

        Args:
            prices: Price array (for reference)
            trades_list: List of Trade objects
            initial_capital: Starting capital

        Returns:
            (return_pct, sharpe_ratio, win_rate)
        """
        if not trades_list:
            return 0, 0, 0

        # Return from total PnL
        total_pnl = sum(t.pnl for t in trades_list)
        return_pct = (total_pnl / initial_capital) * 100

        # Win rate
        wins = sum(1 for t in trades_list if t.pnl > 0)
        win_rate = wins / len(trades_list)

        # Sharpe (simplified: use trade returns)
        trade_returns = np.array([t.pnl / initial_capital for t in trades_list])
        if len(trade_returns) > 1 and np.std(trade_returns) > 0:
            sharpe = np.mean(trade_returns) / np.std(trade_returns) * np.sqrt(252)  # Annualized
        else:
            sharpe = 0

        return return_pct, sharpe, win_rate


def example_walk_forward():
    """Example walk-forward test."""
    print("=" * 80)
    print("WALK-FORWARD TESTING EXAMPLE")
    print("=" * 80)

    # Create sample dataset
    np.random.seed(42)
    total_bars = 500

    # Create windows: 60% train, 40% test
    windows = WalkForwardTester.create_windows(total_bars, train_pct=0.60, test_pct=0.40)

    print(f"\nDataset: {total_bars} bars")
    print(f"Windows: {len(windows)} train/test splits\n")
    print(f"{'Window':<10s} {'Train':<20s} {'Test':<20s} {'Degradation':<15s}")
    print("-" * 80)

    for i, window in enumerate(windows):
        print(f"{i + 1:<10d} [{window.train_start:>3d}-{window.train_end:>3d}] ({window.train_bars:>3d} bars)    "
              f"[{window.test_start:>3d}-{window.test_end:>3d}] ({window.test_bars:>3d} bars)")

    print(f"\nInterpretation:")
    print(f"  - Each window trains on past data, tests on future data")
    print(f"  - Windows slide forward through time")
    print(f"  - If performance is consistent: system is robust")
    print(f"  - If performance degrades: system may be overfitted")


if __name__ == '__main__':
    example_walk_forward()

