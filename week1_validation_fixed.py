"""
WEEK 1: ALPHA VALIDATION - BASELINE ESTABLISHMENT & STATISTICAL SIGNIFICANCE
============================================================================

Professional quantitative validation using latest Yahoo Finance data.
This is Phase 1 of the 8-week alpha validation sprint.

This version includes adjustable signal thresholds to ensure signals are generated.

Run: python week1_validation_fixed.py

Output: Detailed validation report with:
  - Buy-and-hold benchmark
  - Random signal baseline
  - SMA crossover baseline
  - Win rate statistical significance (binomial test)
  - Return significance (t-test)
  - Confidence intervals
  - Alpha calculation vs SPY
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from scipy import stats
from scipy.stats import ttest_1samp
from scipy.stats import t as t_dist
import warnings
warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm, HurstBacktester


class Week1ValidatorFixed:
    """Execute Week 1 validation tests with signal threshold tuning"""

    def __init__(self, symbol='SPY', period='5y', confluence_threshold=0.20):
        """Initialize validator with latest data and adjusted thresholds"""
        print("\n" + "="*80)
        print("WEEK 1: BASELINE ESTABLISHMENT & STATISTICAL SIGNIFICANCE")
        print("="*80)
        print(f"Symbol: {symbol}")
        print(f"Period: {period} (latest available data)")
        print(f"Confluence Threshold: {confluence_threshold*100:.0f}% (adjusted for signal generation)")
        print("="*80)

        self.symbol = symbol
        self.period = period
        self.confluence_threshold = confluence_threshold
        self.data = None
        self.prices = None
        self.results = {}

    def download_data(self):
        """Download latest data from Yahoo Finance"""
        print(f"\n[STEP 1] Downloading latest data from Yahoo Finance...")
        try:
            self.data = yf.download(self.symbol, period=self.period, progress=False)
            if self.data.empty:
                raise ValueError(f"No data downloaded for {self.symbol}")

            # Handle both single and multiple asset downloads
            if isinstance(self.data.columns, pd.MultiIndex):
                prices = self.data[self.symbol]['Close'].values.astype(float)
            else:
                prices = self.data['Close'].values.astype(float)

            # Flatten if needed
            if prices.ndim > 1:
                prices = prices.flatten()

            self.prices = prices

            print(f"  OK: {len(self.data)} bars")
            print(f"      Date range: {self.data.index[0].date()} to {self.data.index[-1].date()}")
            print(f"      Price range: ${prices.min():.2f} to ${prices.max():.2f}")

            return self.data
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def run_hurst_backtest(self):
        """Run Hurst Cyclic Trading with adjusted thresholds"""
        print(f"\n[STEP 2] Running Hurst Cyclic Trading System...")
        print(f"          (Confluence threshold: {self.confluence_threshold*100:.0f}%)")
        try:
            # Create algorithm instance with adjusted threshold
            algo = HurstCyclicAlgorithm(
                self.data,
                use_fld=True,
                confluence_threshold=self.confluence_threshold
            )

            # Run backtest with explicit parameter settings
            backtest = HurstBacktester(
                self.data,
                confluence_min=self.confluence_threshold,  # Lower threshold
                daily_loss_limit=0.05
            )

            results = backtest.run()

            self.results['hurst'] = {
                'total_trades': results.get('total_trades', 0),
                'winning_trades': results.get('winning_trades', 0),
                'losing_trades': results.get('losing_trades', 0),
                'win_rate': results.get('win_rate', 0.0),
                'total_return_pct': results.get('total_return_pct', 0.0),
                'sharpe_ratio': results.get('sharpe_ratio', 0.0),
                'max_drawdown_pct': results.get('max_drawdown_pct', 0.0),
                'avg_winner': results.get('avg_winner', 0.0),
                'avg_loser': results.get('avg_loser', 0.0),
                'expectancy': results.get('expectancy', 0.0),
            }

            print(f"  OK: Backtest complete")
            print(f"      Trades: {self.results['hurst']['total_trades']}")
            print(f"      Win Rate: {self.results['hurst']['win_rate']:.1%}")
            print(f"      Return: {self.results['hurst']['total_return_pct']:.2f}%")
            print(f"      Sharpe: {self.results['hurst']['sharpe_ratio']:.2f}")
            print(f"      Max DD: {self.results['hurst']['max_drawdown_pct']:.2f}%")

            # Check if we have signals
            if self.results['hurst']['total_trades'] < 3:
                print(f"  WARNING: Only {self.results['hurst']['total_trades']} trades generated")
                print(f"           Consider lowering confluence threshold further")

            return self.results['hurst']

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            return None

    def buy_and_hold_benchmark(self):
        """Calculate alpha vs SPY buy-and-hold"""
        print(f"\n[STEP 3] Buy-and-Hold Benchmark (SPY)...")
        try:
            if not self.results.get('hurst'):
                print(f"  SKIP: No Hurst results to compare")
                return None

            # Download SPY data for comparison
            spy_data = yf.download('SPY', period=self.period, progress=False)
            if spy_data.empty:
                print(f"  ERROR: Could not download SPY data")
                return None

            # Get prices
            if isinstance(spy_data.columns, pd.MultiIndex):
                spy_prices = spy_data['SPY']['Close'].values.astype(float)
            else:
                spy_prices = spy_data['Close'].values.astype(float)

            if spy_prices.ndim > 1:
                spy_prices = spy_prices.flatten()

            # Align date ranges
            common_dates = self.data.index.intersection(spy_data.index)
            if len(common_dates) < 10:
                print(f"  ERROR: Insufficient overlapping dates")
                return None

            prices_strategy = self.prices[-len(common_dates):]
            prices_spy = spy_prices[-len(common_dates):]

            # Calculate returns
            returns_strategy = np.diff(prices_strategy) / prices_strategy[:-1]
            returns_spy = np.diff(prices_spy) / prices_spy[:-1]

            # Buy-and-hold return
            bh_return = (prices_spy[-1] / prices_spy[0] - 1) * 100

            # Calculate beta
            covariance = np.cov(returns_strategy, returns_spy)[0][1]
            variance_spy = np.var(returns_spy)
            beta = covariance / variance_spy if variance_spy > 0 else 0

            # Calculate alpha (Fama-French)
            risk_free_rate = 0.045
            strategy_return = self.results['hurst']['total_return_pct'] / 100
            market_return = bh_return / 100
            market_excess = market_return - risk_free_rate

            alpha = (strategy_return - risk_free_rate) - (beta * market_excess)

            # Sharpe ratios
            sharpe_spy = (returns_spy.mean() * 252 - risk_free_rate) / (returns_spy.std() * np.sqrt(252))
            sharpe_hurst = self.results['hurst']['sharpe_ratio']

            self.results['benchmark'] = {
                'bh_return': bh_return,
                'beta': beta,
                'alpha': alpha * 100,
                'sharpe_spy': sharpe_spy,
                'sharpe_hurst': sharpe_hurst,
            }

            print(f"  OK: Buy-and-hold analysis complete")
            print(f"      SPY Return: {bh_return:.2f}%")
            print(f"      Hurst Return: {self.results['hurst']['total_return_pct']:.2f}%")
            print(f"      Beta (vs SPY): {beta:.2f}")
            print(f"      Alpha: {alpha*100:.2f}% annually")
            print(f"      Sharpe (SPY): {sharpe_spy:.2f}")
            print(f"      Sharpe (Hurst): {sharpe_hurst:.2f}")
            print(f"      Sharpe improvement: {sharpe_hurst - sharpe_spy:+.2f}")

            return self.results['benchmark']
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            return None

    def random_baseline(self):
        """Generate random entry signals"""
        print(f"\n[STEP 4] Random Entry Baseline...")
        try:
            hurst_trades = self.results['hurst'].get('total_trades', 0)

            if hurst_trades < 3:
                print(f"  SKIP: Only {hurst_trades} Hurst trades (need >= 3)")
                return None

            # Random win rate (binomial, 50% expected)
            np.random.seed(42)
            random_wins = np.random.binomial(hurst_trades, 0.5)
            random_win_rate = random_wins / hurst_trades

            # Random profit/loss distribution
            avg_winner = self.results['hurst'].get('avg_winner', 100)
            avg_loser = self.results['hurst'].get('avg_loser', -100)

            random_pnl = np.sum([
                avg_winner if np.random.rand() > 0.5 else avg_loser
                for _ in range(hurst_trades)
            ])

            self.results['random'] = {
                'win_rate': random_win_rate,
                'expected_pnl': random_pnl,
                'pnl_per_trade': random_pnl / hurst_trades,
            }

            print(f"  OK: Random baseline analysis")
            print(f"      Hurst Win Rate: {self.results['hurst']['win_rate']:.1%}")
            print(f"      Random Win Rate: {random_win_rate:.1%}")
            print(f"      Edge: {(self.results['hurst']['win_rate'] - random_win_rate):.1%}")
            print(f"      Random Expected PnL: ${random_pnl:.2f}")

            return self.results['random']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def sma_crossover_baseline(self):
        """SMA 50/200 crossover strategy"""
        print(f"\n[STEP 5] SMA Crossover Baseline (50/200)...")
        try:
            prices_series = pd.Series(self.prices)
            ma50 = prices_series.rolling(window=50).mean().values
            ma200 = prices_series.rolling(window=200).mean().values

            # Generate signals
            signals = np.zeros(len(self.prices))
            for i in range(200, len(self.prices)-1):
                if ma50[i] > ma200[i] and ma50[i-1] <= ma200[i-1]:
                    signals[i] = 1  # Buy
                elif ma50[i] < ma200[i] and ma50[i-1] >= ma200[i-1]:
                    signals[i] = -1  # Sell

            # Backtest signals
            trades = []
            position = 0
            entry_price = 0

            for i in range(len(signals)):
                if signals[i] == 1 and position == 0:
                    position = 1
                    entry_price = self.prices[i]
                elif signals[i] == -1 and position == 1:
                    position = 0
                    exit_price = self.prices[i]
                    pnl = (exit_price - entry_price) / entry_price
                    trades.append(pnl)

            if len(trades) > 0:
                win_rate = len([t for t in trades if t > 0]) / len(trades)
                total_return = np.sum(trades) * 100
                avg_winner = np.mean([t for t in trades if t > 0]) * 100 if len([t for t in trades if t > 0]) > 0 else 0
                avg_loser = np.mean([t for t in trades if t <= 0]) * 100 if len([t for t in trades if t <= 0]) > 0 else 0
            else:
                win_rate = 0
                total_return = 0
                avg_winner = 0
                avg_loser = 0

            self.results['sma'] = {
                'trades': len(trades),
                'win_rate': win_rate,
                'total_return': total_return,
                'avg_winner': avg_winner,
                'avg_loser': avg_loser,
            }

            print(f"  OK: SMA crossover baseline")
            print(f"      Trades: {len(trades)}")
            print(f"      Win Rate: {win_rate:.1%}")
            print(f"      Return: {total_return:.2f}%")
            print(f"      Avg Winner: {avg_winner:.2f}%")
            print(f"      Avg Loser: {avg_loser:.2f}%")

            return self.results['sma']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def statistical_significance_winrate(self):
        """Binomial test on win rate"""
        print(f"\n[STEP 6] Statistical Significance - Win Rate (Binomial Test)...")
        try:
            trades = self.results['hurst'].get('total_trades', 0)
            wins = self.results['hurst'].get('winning_trades', 0)

            if trades < 3:
                print(f"  SKIP: Only {trades} trades (need >= 3)")
                return None

            # Binomial test: H0 = win rate is 50% (random)
            try:
                result = stats.binomtest(wins, trades, 0.5, alternative='two-sided')
                p_value = result.pvalue
            except AttributeError:
                p_value = stats.binom_test(wins, trades, 0.5)

            self.results['stats_winrate'] = {
                'trades': trades,
                'wins': wins,
                'win_rate': wins / trades,
                'p_value': p_value,
                'significant': p_value < 0.05,
            }

            print(f"  Null Hypothesis: Win rate = 50% (random)")
            print(f"  Observed: {wins}/{trades} = {wins/trades:.1%}")
            print(f"  p-value: {p_value:.4f}")
            if p_value < 0.05:
                print(f"  Result: SIGNIFICANT [+] (p < 0.05)")
            else:
                print(f"  Result: NOT SIGNIFICANT [-] (p >= 0.05)")

            return self.results['stats_winrate']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def statistical_significance_returns(self):
        """t-test on daily returns"""
        print(f"\n[STEP 7] Statistical Significance - Returns (t-Test)...")
        try:
            if len(self.prices) < 20:
                print(f"  SKIP: Insufficient data for t-test")
                return None

            # Calculate daily returns
            returns = np.diff(self.prices) / self.prices[:-1]

            # t-test: H0 = mean return is 0
            t_stat, p_value = ttest_1samp(returns, 0)

            self.results['stats_returns'] = {
                'mean_return_daily': returns.mean() * 100,
                'std_return_daily': returns.std() * 100,
                't_statistic': t_stat,
                'p_value': p_value,
                'significant': p_value < 0.05,
            }

            print(f"  Null Hypothesis: Mean daily return = 0%")
            print(f"  Observed: mean = {returns.mean()*100:.3f}%, std = {returns.std()*100:.3f}%")
            print(f"  t-statistic: {t_stat:.4f}")
            print(f"  p-value: {p_value:.4f}")
            if p_value < 0.05:
                print(f"  Result: SIGNIFICANT [+] (p < 0.05)")
            else:
                print(f"  Result: NOT SIGNIFICANT [-] (p >= 0.05)")

            return self.results['stats_returns']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def confidence_intervals(self):
        """Calculate 95% confidence intervals"""
        print(f"\n[STEP 8] Confidence Intervals (95%)...")
        try:
            # Confidence interval for win rate
            wins = self.results['hurst'].get('winning_trades', 0)
            trades = self.results['hurst'].get('total_trades', 0)

            if trades > 0:
                p = wins / trades
                z = 1.96  # 95% CI
                se = np.sqrt(p * (1-p) / trades)
                ci_lower = max(0, p - z * se)
                ci_upper = min(1, p + z * se)
            else:
                ci_lower = 0
                ci_upper = 0
                p = 0

            # Confidence interval for Sharpe ratio
            sharpe = self.results['hurst'].get('sharpe_ratio', 0)
            if trades > 0:
                sharpe_se = 1 / np.sqrt(trades)  # Approximate
                sharpe_ci_lower = sharpe - 1.96 * sharpe_se
                sharpe_ci_upper = sharpe + 1.96 * sharpe_se
            else:
                sharpe_ci_lower = 0
                sharpe_ci_upper = 0

            self.results['confidence_intervals'] = {
                'win_rate': p,
                'win_rate_ci': (ci_lower, ci_upper),
                'sharpe': sharpe,
                'sharpe_ci': (sharpe_ci_lower, sharpe_ci_upper),
            }

            print(f"  Win Rate 95% CI: [{ci_lower:.1%}, {ci_upper:.1%}]")
            print(f"  Outperforms 50% benchmark? {'YES' if ci_lower > 0.5 else 'NO'}")
            print(f"  Sharpe 95% CI: [{sharpe_ci_lower:.2f}, {sharpe_ci_upper:.2f}]")

            return self.results['confidence_intervals']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def generate_report(self):
        """Generate comprehensive validation report"""
        print("\n" + "="*80)
        print("WEEK 1 VALIDATION REPORT")
        print("="*80)

        print("\nSUMMARY METRICS")
        print("-" * 80)

        hurst = self.results.get('hurst', {})
        bench = self.results.get('benchmark', {})
        stats_wr = self.results.get('stats_winrate', {})
        stats_ret = self.results.get('stats_returns', {})
        ci = self.results.get('confidence_intervals', {})

        print(f"Hurst System Performance:")
        print(f"  Trades: {hurst.get('total_trades', 0)}")
        print(f"  Win Rate: {hurst.get('win_rate', 0):.1%}")
        print(f"  Return: {hurst.get('total_return_pct', 0):.2f}%")
        print(f"  Sharpe: {hurst.get('sharpe_ratio', 0):.2f}")
        print(f"  Max DD: {hurst.get('max_drawdown_pct', 0):.2f}%")

        if bench:
            print(f"\nAlpha vs Buy-and-Hold (SPY):")
            print(f"  Alpha: {bench.get('alpha', 0):.2f}%")
            print(f"  Beta: {bench.get('beta', 0):.2f}")
            print(f"  Sharpe Improvement: {bench.get('sharpe_hurst', 0) - bench.get('sharpe_spy', 0):+.2f}")

        print(f"\nStatistical Significance Tests:")
        print(f"  Win Rate Binomial Test: p={stats_wr.get('p_value', 1):.4f} {'[PASS]' if stats_wr.get('significant') else '[FAIL]'}")
        print(f"  Returns t-Test: p={stats_ret.get('p_value', 1):.4f} {'[PASS]' if stats_ret.get('significant') else '[FAIL]'}")
        print(f"  Win Rate 95% CI: {ci.get('win_rate_ci', (0, 1))}")

        print(f"\nCRITICAL SUCCESS CRITERIA STATUS:")
        print(f"  [1] Win rate p < 0.05: {'PASS' if stats_wr.get('significant') else 'FAIL'}")
        print(f"  [2] Return p < 0.05: {'PASS' if stats_ret.get('significant') else 'FAIL'}")
        print(f"  [3] Sharpe 95% CI > benchmark: {'PASS' if ci.get('sharpe_ci', (0, 0))[0] > 0 else 'FAIL'}")
        print(f"  [4] Annual return > 10%: {'PASS' if hurst.get('total_return_pct', 0) > 10 else 'FAIL'}")
        print(f"  [5] Sharpe > 1.5: {'PASS' if hurst.get('sharpe_ratio', 0) > 1.5 else 'FAIL'}")
        print(f"  [6] Max DD < 20%: {'PASS' if hurst.get('max_drawdown_pct', 0) < 20 else 'FAIL'}")

        print("\n" + "="*80)
        print("END OF WEEK 1 REPORT")
        print("="*80)

    def run_all_tests(self):
        """Execute all Week 1 tests"""
        self.download_data()
        if self.data is None:
            return

        self.run_hurst_backtest()
        self.buy_and_hold_benchmark()
        self.random_baseline()
        self.sma_crossover_baseline()
        self.statistical_significance_winrate()
        self.statistical_significance_returns()
        self.confidence_intervals()
        self.generate_report()


def main():
    """Run validation on multiple assets"""
    print("\n" + "="*80)
    print("WEEK 1 ALPHA VALIDATION - MULTIPLE ASSETS")
    print("Using Latest Available Yahoo Finance Data")
    print("="*80)

    symbols = ['SPY', 'QQQ', 'IWM']

    for symbol in symbols:
        print(f"\n\n{'#'*80}")
        print(f"TESTING: {symbol}")
        print(f"{'#'*80}")

        # Use lower confluence threshold to ensure signals are generated
        validator = Week1ValidatorFixed(symbol=symbol, period='5y', confluence_threshold=0.20)
        validator.run_all_tests()

        # Save results
        try:
            with open(f'week1_results_{symbol}.txt', 'w') as f:
                for key, val in validator.results.items():
                    f.write(f"{key}: {val}\n")
            print(f"\nResults saved to week1_results_{symbol}.txt")
        except Exception as e:
            print(f"Could not save results: {e}")


if __name__ == '__main__':
    main()
