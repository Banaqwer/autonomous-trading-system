"""
Monte Carlo Simulation for Hurst Algorithm
==========================================

Tests robustness by randomly shuffling trade sequences.

Hypothesis: If system has genuine edge, results should be positive
across most random orderings. If edge is brittle, most shuffles fail.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Trade:
    """Single trade result."""
    pnl: float
    r_multiple: float
    side: str


@dataclass
class MonteCarloResult:
    """Results from one MC iteration."""
    total_pnl: float
    win_rate: float
    sharpe: float
    max_dd: float
    final_equity: float


class MonteCarloSimulator:
    """
    Shuffle trade sequences randomly and measure robustness.

    Logic:
    1. Extract actual trades from backtest
    2. For N iterations (typically 1000):
       a. Randomly shuffle trade order
       b. Recalculate equity curve
       c. Compute metrics (Sharpe, max DD, etc)
    3. Analyze distribution of results
    """

    @staticmethod
    def extract_trades_from_results(trades_list) -> List[Trade]:
        """Convert backtest trades to Monte Carlo format."""
        mc_trades = []
        for trade in trades_list:
            mc_trades.append(Trade(
                pnl=trade.pnl,
                r_multiple=trade.r_multiple,
                side=trade.side.value if hasattr(trade.side, 'value') else trade.side
            ))
        return mc_trades

    @staticmethod
    def simulate_equity_curve(trades: List[Trade], initial_capital: float = 100000) -> Tuple[float, float, float]:
        """
        Simulate equity curve from trades in given order.

        Returns: (final_equity, max_drawdown, equity_list)
        """
        equity = initial_capital
        equity_curve = [equity]
        peak_equity = equity
        max_dd = 0

        for trade in trades:
            equity += trade.pnl
            equity_curve.append(equity)

            # Track drawdown
            if equity < peak_equity:
                dd = (equity - peak_equity) / peak_equity
                max_dd = min(max_dd, dd)
            else:
                peak_equity = equity

        return equity, max_dd, equity_curve

    @staticmethod
    def calculate_sharpe(equity_curve: List[float]) -> float:
        """Calculate Sharpe ratio from equity curve."""
        if len(equity_curve) < 2:
            return 0

        returns = np.diff(equity_curve) / np.array(equity_curve[:-1])

        if len(returns) == 0 or np.std(returns) == 0:
            return 0

        # Annualized Sharpe (assuming daily bars, ~252 trading days per year)
        annual_return = np.mean(returns) * 252
        annual_std = np.std(returns) * np.sqrt(252)

        if annual_std == 0:
            return 0

        return annual_return / annual_std

    @staticmethod
    def run_monte_carlo(
        trades: List[Trade],
        initial_capital: float = 100000,
        iterations: int = 1000,
        random_seed: int = 42
    ) -> Tuple[List[MonteCarloResult], dict]:
        """
        Run Monte Carlo simulation.

        Args:
            trades: List of Trade objects
            initial_capital: Starting capital
            iterations: Number of shuffles to run
            random_seed: For reproducibility

        Returns:
            (results_list, summary_stats)
        """
        np.random.seed(random_seed)

        results = []

        for i in range(iterations):
            # Shuffle trades
            shuffled_trades = list(np.random.permutation(trades))

            # Simulate equity
            final_equity, max_dd, equity_curve = MonteCarloSimulator.simulate_equity_curve(
                shuffled_trades, initial_capital
            )

            # Calculate metrics
            total_pnl = final_equity - initial_capital
            return_pct = (total_pnl / initial_capital) * 100

            # Win rate
            wins = sum(1 for t in shuffled_trades if t.pnl > 0)
            win_rate = wins / len(shuffled_trades) if shuffled_trades else 0

            # Sharpe
            sharpe = MonteCarloSimulator.calculate_sharpe(equity_curve)

            # Store result
            results.append(MonteCarloResult(
                total_pnl=total_pnl,
                win_rate=win_rate,
                sharpe=sharpe,
                max_dd=max_dd,
                final_equity=final_equity
            ))

        # Calculate statistics
        pnls = [r.total_pnl for r in results]
        sharpes = [r.sharpe for r in results]
        win_rates = [r.win_rate for r in results]
        max_dds = [r.max_dd for r in results]

        stats = {
            'pnl_mean': np.mean(pnls),
            'pnl_std': np.std(pnls),
            'pnl_percentile_5': np.percentile(pnls, 5),
            'pnl_percentile_25': np.percentile(pnls, 25),
            'pnl_median': np.median(pnls),
            'pnl_percentile_75': np.percentile(pnls, 75),
            'pnl_percentile_95': np.percentile(pnls, 95),
            'pnl_min': min(pnls),
            'pnl_max': max(pnls),
            'profitable_runs': sum(1 for p in pnls if p > 0),
            'profitable_pct': sum(1 for p in pnls if p > 0) / len(pnls) * 100,
            'sharpe_mean': np.mean(sharpes),
            'sharpe_std': np.std(sharpes),
            'win_rate_mean': np.mean(win_rates),
            'win_rate_std': np.std(win_rates),
            'max_dd_mean': np.mean(max_dds),
            'max_dd_min': min(max_dds),  # least negative
            'max_dd_max': max(max_dds),   # most negative
        }

        return results, stats


if __name__ == '__main__':
    # Example: Create dummy trades and run simulation
    np.random.seed(42)

    # Create sample trades: 16 trades with realistic distribution
    sample_trades = []
    for i in range(16):
        if i < 14:  # 14 winners
            pnl = np.random.uniform(5, 50)
        else:  # 2 losers
            pnl = np.random.uniform(-10, -5)
        sample_trades.append(Trade(pnl=pnl, r_multiple=pnl/100, side='long'))

    print("=" * 80)
    print("MONTE CARLO SIMULATION TEST")
    print("=" * 80)
    print(f"\nInput: {len(sample_trades)} trades (14 winners, 2 losers)")
    print(f"Running 1000 simulations with random shuffle...\n")

    results, stats = MonteCarloSimulator.run_monte_carlo(sample_trades, iterations=1000)

    print(f"PnL Distribution:")
    print(f"  Min:        {stats['pnl_min']:>8.2f}")
    print(f"  5th %ile:   {stats['pnl_percentile_5']:>8.2f}")
    print(f"  25th %ile:  {stats['pnl_percentile_25']:>8.2f}")
    print(f"  Median:     {stats['pnl_median']:>8.2f}")
    print(f"  75th %ile:  {stats['pnl_percentile_75']:>8.2f}")
    print(f"  95th %ile:  {stats['pnl_percentile_95']:>8.2f}")
    print(f"  Max:        {stats['pnl_max']:>8.2f}")
    print(f"  Profitable: {stats['profitable_pct']:.1f}% of runs")

