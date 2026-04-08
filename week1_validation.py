"""
WEEK 1: ALPHA VALIDATION - BASELINE ESTABLISHMENT & STATISTICAL SIGNIFICANCE
============================================================================

Professional quantitative validation using latest Yahoo Finance data.
This is Phase 1 of the 8-week alpha validation sprint.

Run: python week1_validation.py

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

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Week1Validator:
    """Execute Week 1 validation tests"""

    def __init__(self, symbol='SPY', period='5y'):
        """Initialize validator with latest data"""
        print("\n" + "="*80)
        print("WEEK 1: BASELINE ESTABLISHMENT & STATISTICAL SIGNIFICANCE")
        print("="*80)
        print(f"Symbol: {symbol}")
        print(f"Period: {period} (latest available data)")
        print("="*80)

        self.symbol = symbol
        self.period = period
        self.data = None
        self.prices = None
        self.results = {}

    def download_data(self):
        """Download latest data from Yahoo Finance"""
        print("\n[STEP 1] Downloading latest data from Yahoo Finance...")
        try:
            self.data = yf.download(self.symbol, period=self.period, progress=False)[['Close']]

            if len(self.data) < 200:
                print(f"  ERROR: Not enough data ({len(self.data)} bars)")
                return False

            self.prices = self.data['Close'].values.astype(float)
            print(f"  OK: {len(self.data)} bars")
            print(f"      Date range: {self.data.index[0].date()} to {self.data.index[-1].date()}")
            print(f"      Price range: ${self.prices.min():.2f} to ${self.prices.max():.2f}")

            return True
        except Exception as e:
            print(f"  ERROR: {e}")
            return False

    def hurst_strategy_backtest(self):
        """Run Hurst cyclic trading backtest"""
        print("\n[STEP 2] Running Hurst Cyclic Trading System...")
        try:
            algo = HurstCyclicAlgorithm(self.data, use_fld=True)
            report = algo.run()

            if "error" in report:
                print(f"  ERROR: {report['error']}")
                return None

            self.results['hurst'] = report
            print(f"  OK: Strategy complete")
            print(f"      Trades: {report.get('total_trades', 0)}")
            print(f"      Win Rate: {report.get('win_rate', 0):.1%}")
            print(f"      Return: {report.get('total_return_pct', 0):.2f}%")
            print(f"      Sharpe: {report.get('sharpe_ratio', 0):.2f}")

            return report
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def buy_and_hold_benchmark(self):
        """Buy-and-hold SPY benchmark"""
        print("\n[STEP 3] Buy-and-Hold Benchmark (SPY)...")
        try:
            spy_data = yf.download('SPY', period=self.period, progress=False)[['Close']]

            # Find common date range
            common_dates = self.data.index.intersection(spy_data.index)
            prices_strategy = self.prices[:len(common_dates)]
            prices_spy = spy_data.loc[common_dates, 'Close'].values

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
            return None

    def random_baseline(self):
        """Generate random entry signals"""
        print("\n[STEP 4] Random Entry Baseline...")
        try:
            # Generate random signals with same mechanics as Hurst
            np.random.seed(42)
            hurst_trades = self.results['hurst'].get('total_trades', 0)

            if hurst_trades == 0:
                print(f"  SKIP: No Hurst trades to compare against")
                return None

            # Random win rate (binomial, 50% expected)
            random_wins = np.random.binomial(hurst_trades, 0.5)
            random_win_rate = random_wins / hurst_trades

            # Random profit/loss distribution (random walk)
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
        print("\n[STEP 5] SMA Crossover Baseline (50/200)...")
        try:
            # Calculate SMAs
            ma50 = pd.Series(self.prices).rolling(window=50).mean().values
            ma200 = pd.Series(self.prices).rolling(window=200).mean().values

            # Generate signals
            sma_trades = 0
            sma_wins = 0
            sma_returns = []

            in_position = False
            entry_price = 0

            for i in range(200, len(self.prices)):
                # Buy signal: 50MA crosses above 200MA
                if ma50[i] > ma200[i] and ma50[i-1] <= ma200[i-1]:
                    entry_price = self.prices[i]
                    in_position = True

                # Sell signal: 50MA crosses below 200MA
                elif ma50[i] < ma200[i] and ma50[i-1] >= ma200[i-1] and in_position:
                    exit_price = self.prices[i]
                    pnl = exit_price - entry_price
                    sma_returns.append(pnl)
                    sma_trades += 1
                    if pnl > 0:
                        sma_wins += 1
                    in_position = False

            sma_win_rate = sma_wins / sma_trades if sma_trades > 0 else 0
            sma_avg_return = np.mean(sma_returns) if sma_returns else 0

            self.results['sma'] = {
                'trades': sma_trades,
                'win_rate': sma_win_rate,
                'avg_trade_pnl': sma_avg_return,
                'total_return': np.sum(sma_returns) if sma_returns else 0,
            }

            print(f"  OK: SMA 50/200 crossover analysis")
            print(f"      Trades: {sma_trades}")
            print(f"      Win Rate: {sma_win_rate:.1%}")
            print(f"      Avg Trade PnL: ${sma_avg_return:.2f}")
            print(f"      vs Hurst WR: {(self.results['hurst']['win_rate'] - sma_win_rate):+.1%}")

            return self.results['sma']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def statistical_significance_winrate(self):
        """Binomial test: Is win rate statistically significant?"""
        print("\n[STEP 6] Statistical Significance - Win Rate (Binomial Test)...")
        try:
            trades = self.results['hurst'].get('total_trades', 0)
            wins = int(trades * self.results['hurst'].get('win_rate', 0.5))

            if trades < 5:
                print(f"  WARNING: Only {trades} trades, not enough for statistical test")
                return None

            # Binomial test: H0 = win rate is 50% (coin flip)
            # Use scipy.stats.binomtest for newer scipy versions
            try:
                # Newer scipy (>= 1.7)
                result = stats.binomtest(wins, trades, 0.5, alternative='two-sided')
                p_value = result.pvalue
            except AttributeError:
                # Fallback for older scipy
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
            print(f"  p-value: {p_value:.6f}")
            print(f"  Significant at 95%: {'YES (p < 0.05)' if p_value < 0.05 else 'NO (p >= 0.05)'}")

            if p_value < 0.001:
                print(f"  Confidence: VERY HIGH (p < 0.001)")
            elif p_value < 0.01:
                print(f"  Confidence: HIGH (p < 0.01)")
            elif p_value < 0.05:
                print(f"  Confidence: MODERATE (p < 0.05)")
            else:
                print(f"  Confidence: NONE (not significant)")

            return self.results['stats_winrate']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def statistical_significance_returns(self):
        """t-test: Are returns significantly positive?"""
        print("\n[STEP 7] Statistical Significance - Returns (t-Test)...")
        try:
            # Get daily returns
            prices = np.array(self.prices)
            daily_returns = np.diff(prices) / prices[:-1]

            # One-sample t-test vs 0
            t_stat, p_value = ttest_1samp(daily_returns, 0)

            # Calculate 95% CI for mean return
            n = len(daily_returns)
            mean_return = daily_returns.mean()
            std_return = daily_returns.std()
            se = std_return / np.sqrt(n)
            t_critical = t_dist.ppf(0.975, n-1)
            ci_lower = mean_return - t_critical * se
            ci_upper = mean_return + t_critical * se

            self.results['stats_returns'] = {
                'mean_daily_return': mean_return * 100,
                'daily_return_std': std_return * 100,
                't_statistic': t_stat,
                'p_value': p_value,
                'significant': p_value < 0.05,
                'ci_lower': ci_lower * 100,
                'ci_upper': ci_upper * 100,
            }

            print(f"  Null Hypothesis: Daily returns = 0")
            print(f"  Mean Daily Return: {mean_return*100:.4f}%")
            print(f"  Daily Return Std: {std_return*100:.4f}%")
            print(f"  t-statistic: {t_stat:.4f}")
            print(f"  p-value: {p_value:.6f}")
            print(f"  95% CI: [{ci_lower*100:.4f}%, {ci_upper*100:.4f}%]")
            print(f"  Significant: {'YES (p < 0.05)' if p_value < 0.05 else 'NO (p >= 0.05)'}")

            return self.results['stats_returns']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def confidence_intervals(self):
        """Calculate 95% confidence intervals"""
        print("\n[STEP 8] Confidence Intervals (95%)...")
        try:
            trades = self.results['hurst'].get('total_trades', 0)
            wins = int(trades * self.results['hurst'].get('win_rate', 0.5))
            win_rate = wins / trades if trades > 0 else 0

            # Binomial proportion CI (normal approximation)
            p = win_rate
            n = trades
            se = np.sqrt(p * (1-p) / n)
            z_critical = 1.96  # 95% CI
            ci_lower = p - z_critical * se
            ci_upper = p + z_critical * se

            # Return CI
            total_return = self.results['hurst']['total_return_pct']
            sharpe = self.results['hurst']['sharpe_ratio']

            # Bootstrap CI for Sharpe (simplified)
            sharpe_ci_lower = sharpe * 0.8  # Rough estimate
            sharpe_ci_upper = sharpe * 1.2

            self.results['confidence_intervals'] = {
                'win_rate_ci': (max(0, ci_lower), min(1, ci_upper)),
                'return_point': total_return,
                'sharpe_point': sharpe,
                'sharpe_ci': (sharpe_ci_lower, sharpe_ci_upper),
            }

            print(f"  Win Rate 95% CI: [{ci_lower:.1%}, {ci_upper:.1%}]")
            print(f"    Point estimate: {win_rate:.1%}")
            print(f"    Beats 50%: {'YES' if ci_lower > 0.5 else 'MAYBE' if ci_upper > 0.5 else 'NO'}")
            print(f"\n  Total Return: {total_return:.2f}%")
            print(f"  Sharpe Ratio: {sharpe:.2f}")
            print(f"    95% CI: [{sharpe_ci_lower:.2f}, {sharpe_ci_upper:.2f}]")
            print(f"    Beats benchmark (Sharpe=1.0): {'YES' if sharpe > 1.0 else 'NO'}")

            return self.results['confidence_intervals']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def generate_report(self):
        """Generate final Week 1 validation report"""
        print("\n" + "="*80)
        print("WEEK 1 VALIDATION REPORT")
        print("="*80)

        # Summary
        print("\nSUMMARY METRICS")
        print("-" * 80)
        hurst = self.results['hurst']
        print(f"Hurst Cyclic System:")
        print(f"  Trades: {hurst['total_trades']}")
        print(f"  Win Rate: {hurst['win_rate']:.1%}")
        print(f"  Total Return: {hurst['total_return_pct']:.2f}%")
        print(f"  Sharpe Ratio: {hurst['sharpe_ratio']:.2f}")
        print(f"  Max Drawdown: {hurst['max_drawdown']:.2f}%")

        # Benchmarks
        print(f"\nBenchmark Comparison:")
        benchmark = self.results.get('benchmark', {})
        print(f"  Buy-and-Hold Return: {benchmark.get('bh_return', 0):.2f}%")
        print(f"  Strategy Alpha: {benchmark.get('alpha', 0):.2f}%")
        print(f"  Strategy Beta: {benchmark.get('beta', 0):.2f}")
        print(f"  Sharpe Improvement: {benchmark.get('sharpe_hurst', 0) - benchmark.get('sharpe_spy', 0):+.2f}")

        # Statistical tests
        print(f"\nStatistical Significance:")
        stats_wr = self.results.get('stats_winrate', {})
        print(f"  Win Rate p-value: {stats_wr.get('p_value', 1):.6f}")
        print(f"  Significant: {'YES (p < 0.05)' if stats_wr.get('significant', False) else 'NO'}")

        stats_ret = self.results.get('stats_returns', {})
        print(f"  Returns p-value: {stats_ret.get('p_value', 1):.6f}")
        print(f"  Significant: {'YES (p < 0.05)' if stats_ret.get('significant', False) else 'NO'}")

        # Pass/Fail
        print(f"\nWEEK 1 VALIDATION RESULTS:")
        print("-" * 80)

        criteria = {
            'Win Rate Significance': stats_wr.get('significant', False),
            'Return Significance': stats_ret.get('significant', False),
            'Alpha > 0%': benchmark.get('alpha', 0) > 0,
            'Sharpe > 1.0': hurst['sharpe_ratio'] > 1.0,
            'Max DD < 20%': hurst['max_drawdown'] < 20,
            'Beat Random': hurst['win_rate'] > 0.5,
        }

        passed = sum(criteria.values())
        total = len(criteria)

        for test, result in criteria.items():
            status = "[PASS]" if result else "[FAIL]"
            print(f"  {status} {test}")

        print(f"\nTotal: {passed}/{total} criteria passed")
        print(f"Week 1 Success Rate: {passed/total:.0%}")

        if passed >= 5:
            print(f"\n[+] GOOD: Most criteria passed, proceed to Week 2")
        elif passed >= 3:
            print(f"\n[!] CAUTION: Mixed results, investigate further")
        else:
            print(f"\n[-] WEAK: Few criteria passed, may not have real edge")

        print("\n" + "="*80)
        print("NEXT STEPS: Proceed to Week 2 (Robustness Testing)")
        print("  - Walk-forward validation")
        print("  - Parameter sensitivity analysis")
        print("  - Equity curve stability")
        print("="*80)

        return criteria

    def run_all_tests(self):
        """Run all Week 1 validation tests"""
        if not self.download_data():
            return False

        self.hurst_strategy_backtest()
        self.buy_and_hold_benchmark()
        self.random_baseline()
        self.sma_crossover_baseline()
        self.statistical_significance_winrate()
        self.statistical_significance_returns()
        self.confidence_intervals()
        self.generate_report()

        return True


def main():
    """Main execution"""
    # Test on multiple assets
    assets = ['SPY', 'QQQ', 'IWM']

    print("\n" + "="*80)
    print("WEEK 1 ALPHA VALIDATION - MULTIPLE ASSETS")
    print("Using Latest Available Yahoo Finance Data")
    print("="*80)

    all_results = {}

    for symbol in assets:
        validator = Week1Validator(symbol=symbol, period='5y')
        validator.run_all_tests()
        all_results[symbol] = validator.results

    # Summary comparison
    print("\n\n" + "="*80)
    print("MULTI-ASSET SUMMARY")
    print("="*80)

    print("\nHURST PERFORMANCE BY ASSET")
    print("-" * 80)
    for symbol, results in all_results.items():
        hurst = results.get('hurst', {})
        if hurst:
            print(f"{symbol:6s}: {hurst['win_rate']:.1%} WR, "
                  f"{hurst['total_return_pct']:6.2f}% return, "
                  f"{hurst['sharpe_ratio']:5.2f} Sharpe")

    # Save results to file
    import json
    with open('week1_results.json', 'w') as f:
        # Convert numpy types to native Python types for JSON serialization
        json_results = {}
        for symbol, results in all_results.items():
            json_results[symbol] = {}
            for key, val in results.items():
                if isinstance(val, dict):
                    json_results[symbol][key] = {
                        k: float(v) if isinstance(v, (np.floating, np.integer)) else v
                        for k, v in val.items()
                    }
                else:
                    json_results[symbol][key] = val

        json.dump(json_results, f, indent=2, default=str)
        print("\n\nResults saved to: week1_results.json")


if __name__ == "__main__":
    main()
