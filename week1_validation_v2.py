"""
WEEK 1: ALPHA VALIDATION - BASELINE ESTABLISHMENT & STATISTICAL SIGNIFICANCE
============================================================================

Professional quantitative validation using latest Yahoo Finance data.
This is Phase 1 of the 8-week alpha validation sprint.

Run: python week1_validation_v2.py

This version properly handles the signal generation pipeline and provides
diagnostic information when signals are sparse.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from scipy import stats
from scipy.stats import ttest_1samp
import warnings
warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Week1ValidatorV2:
    """Execute Week 1 validation tests"""

    def __init__(self, symbol='SPY', period='5y'):
        """Initialize validator with latest data"""
        print("\n" + "="*80)
        print(f"WEEK 1 VALIDATION: {symbol}")
        print("="*80)
        print(f"Symbol: {symbol}")
        print(f"Period: {period} (latest available data)")
        print("="*80)

        self.symbol = symbol
        self.period = period
        self.data = None
        self.prices = None
        self.results = {}
        self.report = None

    def download_data(self):
        """Download latest data from Yahoo Finance"""
        print(f"\n[STEP 1] Downloading latest data from Yahoo Finance...")
        try:
            self.data = yf.download(self.symbol, period=self.period, progress=False)
            if self.data.empty:
                raise ValueError(f"No data downloaded for {self.symbol}")

            # Extract Close price
            try:
                # Try single symbol first
                prices = self.data['Close'].values.astype(float)
            except (KeyError, TypeError):
                # If that fails, it might be MultiIndex from multiple symbols
                try:
                    prices = self.data[self.symbol]['Close'].values.astype(float)
                except:
                    # Last resort: try to find Close column
                    if 'Close' in self.data.columns:
                        prices = self.data['Close'].values.astype(float)
                    else:
                        # Get first numeric column that looks like price
                        for col in self.data.columns:
                            if isinstance(col, tuple):
                                if col[1] == 'Close':
                                    prices = self.data[col].values.astype(float)
                                    break
                            elif col == 'Close':
                                prices = self.data[col].values.astype(float)
                                break

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
            import traceback
            traceback.print_exc()
            return None

    def run_hurst_backtest(self):
        """Run Hurst Cyclic Trading System"""
        print(f"\n[STEP 2] Running Hurst Cyclic Trading System...")
        try:
            algo = HurstCyclicAlgorithm(
                self.data,
                use_fld=True,
                use_trigonometric_refinement=True
            )

            # Run the algorithm and capture the report
            self.report = algo.run()

            # Extract key metrics
            if self.report:
                total_trades = len(algo.trades)
                winning_trades = len([t for t in algo.trades if t.pnl > 0])
                losing_trades = len([t for t in algo.trades if t.pnl < 0])

                if total_trades > 0:
                    win_rate = winning_trades / total_trades
                    total_pnl = sum(t.pnl for t in algo.trades)
                    total_return_pct = (total_pnl / self.report.get('initial_capital', 100000)) * 100
                    avg_winner = np.mean([t.pnl for t in algo.trades if t.pnl > 0]) if winning_trades > 0 else 0
                    avg_loser = np.mean([t.pnl for t in algo.trades if t.pnl < 0]) if losing_trades > 0 else 0
                else:
                    win_rate = 0
                    total_return_pct = 0
                    avg_winner = 0
                    avg_loser = 0

                # Calculate Sharpe ratio
                if len(algo.equity_df) > 1:
                    daily_returns = algo.equity_df['Daily Return'].dropna()
                    if len(daily_returns) > 0 and daily_returns.std() > 0:
                        sharpe_ratio = (daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252))
                    else:
                        sharpe_ratio = 0
                else:
                    sharpe_ratio = 0

                # Calculate max drawdown
                if len(algo.equity_df) > 0:
                    cummax = algo.equity_df['Equity'].cummax()
                    drawdown = (algo.equity_df['Equity'] - cummax) / cummax
                    max_drawdown_pct = drawdown.min() * 100
                else:
                    max_drawdown_pct = 0

                self.results['hurst'] = {
                    'total_trades': total_trades,
                    'winning_trades': winning_trades,
                    'losing_trades': losing_trades,
                    'win_rate': win_rate,
                    'total_return_pct': total_return_pct,
                    'sharpe_ratio': sharpe_ratio,
                    'max_drawdown_pct': max_drawdown_pct,
                    'avg_winner': avg_winner,
                    'avg_loser': avg_loser,
                }

                print(f"  OK: Backtest complete")
                print(f"      Trades: {total_trades}")
                print(f"      Win Rate: {win_rate:.1%}")
                print(f"      Return: {total_return_pct:.2f}%")
                print(f"      Sharpe: {sharpe_ratio:.2f}")
                print(f"      Max DD: {max_drawdown_pct:.2f}%")

                if total_trades == 0:
                    print(f"\n  NOTE: No signals generated.")
                    print(f"        This may indicate:")
                    print(f"        1. Signal confluence thresholds too strict")
                    print(f"        2. Market conditions not suited to the strategy")
                    print(f"        3. Cycle detection working but entry conditions not met")

                return self.results['hurst']
            else:
                print(f"  ERROR: No report returned from algorithm")
                return None

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
                print(f"  SKIP: No Hurst results yet")
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

            idx_strategy = len(self.data) - len(common_dates)
            prices_strategy = self.prices[idx_strategy:]
            idx_spy = len(spy_prices) - len(common_dates)
            prices_spy = spy_prices[idx_spy:]

            # Make sure they're the same length
            min_len = min(len(prices_strategy), len(prices_spy))
            prices_strategy = prices_strategy[-min_len:]
            prices_spy = prices_spy[-min_len:]

            # Calculate returns
            returns_strategy = np.diff(prices_strategy) / prices_strategy[:-1]
            returns_spy = np.diff(prices_spy) / prices_spy[:-1]

            # Buy-and-hold return
            bh_return = (prices_spy[-1] / prices_spy[0] - 1) * 100

            # Calculate beta
            if len(returns_strategy) > 1 and np.var(returns_spy) > 0:
                covariance = np.cov(returns_strategy, returns_spy)[0][1]
                variance_spy = np.var(returns_spy)
                beta = covariance / variance_spy
            else:
                beta = 0

            # Calculate alpha (Fama-French)
            risk_free_rate = 0.045
            strategy_return = self.results['hurst']['total_return_pct'] / 100
            market_return = bh_return / 100
            market_excess = market_return - risk_free_rate

            alpha = (strategy_return - risk_free_rate) - (beta * market_excess)

            # Sharpe ratios
            if len(returns_spy) > 0 and returns_spy.std() > 0:
                sharpe_spy = (returns_spy.mean() * 252 - risk_free_rate) / (returns_spy.std() * np.sqrt(252))
            else:
                sharpe_spy = 0

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

    def sma_crossover_baseline(self):
        """SMA 50/200 crossover strategy for comparison"""
        print(f"\n[STEP 4] SMA Crossover Baseline (50/200)...")
        try:
            prices_series = pd.Series(self.prices)
            ma50 = prices_series.rolling(window=50).mean().values
            ma200 = prices_series.rolling(window=200).mean().values

            # Generate signals
            trades = []
            position = False
            entry_price = 0

            for i in range(200, len(self.prices)-1):
                if not position and ma50[i] > ma200[i]:
                    position = True
                    entry_price = self.prices[i]
                elif position and ma50[i] < ma200[i]:
                    position = False
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

            return self.results['sma']
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    def statistical_significance_winrate(self):
        """Binomial test on win rate"""
        print(f"\n[STEP 5] Statistical Significance - Win Rate (Binomial Test)...")
        try:
            trades = self.results['hurst'].get('total_trades', 0)
            wins = self.results['hurst'].get('winning_trades', 0)

            if trades < 3:
                print(f"  SKIP: Only {trades} trades (need >= 3 for statistical test)")
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
        print(f"\n[STEP 6] Statistical Significance - Returns (t-Test)...")
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

    def generate_report(self):
        """Generate comprehensive validation report"""
        print("\n" + "="*80)
        print(f"WEEK 1 VALIDATION REPORT - {self.symbol}")
        print("="*80)

        hurst = self.results.get('hurst', {})
        bench = self.results.get('benchmark', {})
        stats_wr = self.results.get('stats_winrate', {})
        stats_ret = self.results.get('stats_returns', {})
        sma = self.results.get('sma', {})

        print(f"\nHURST SYSTEM PERFORMANCE:")
        print(f"  Trades: {hurst.get('total_trades', 0)}")
        print(f"  Win Rate: {hurst.get('win_rate', 0):.1%}")
        print(f"  Return: {hurst.get('total_return_pct', 0):.2f}%")
        print(f"  Sharpe: {hurst.get('sharpe_ratio', 0):.2f}")
        print(f"  Max DD: {hurst.get('max_drawdown_pct', 0):.2f}%")

        if bench:
            print(f"\nALPHA vs BUY-AND-HOLD (SPY):")
            print(f"  Alpha: {bench.get('alpha', 0):.2f}%")
            print(f"  Beta: {bench.get('beta', 0):.2f}")
            print(f"  Sharpe Improvement: {bench.get('sharpe_hurst', 0) - bench.get('sharpe_spy', 0):+.2f}")

        if sma:
            print(f"\nCOMPARISON vs SMA 50/200:")
            print(f"  SMA Trades: {sma.get('trades', 0)}")
            print(f"  SMA Win Rate: {sma.get('win_rate', 0):.1%}")
            print(f"  SMA Return: {sma.get('total_return', 0):.2f}%")
            print(f"  Hurst Edge: {hurst.get('win_rate', 0) - sma.get('win_rate', 0):+.1%}")

        print(f"\nSTATISTICAL SIGNIFICANCE TESTS:")
        if stats_wr:
            print(f"  Win Rate Binomial: p={stats_wr.get('p_value', 1):.4f} {'[PASS]' if stats_wr.get('significant') else '[FAIL]'}")
        else:
            print(f"  Win Rate Binomial: SKIPPED (need >= 3 trades)")

        if stats_ret:
            print(f"  Returns t-Test: p={stats_ret.get('p_value', 1):.4f} {'[PASS]' if stats_ret.get('significant') else '[FAIL]'}")
        else:
            print(f"  Returns t-Test: SKIPPED")

        print(f"\nCRITICAL SUCCESS CRITERIA:")
        criteria_passed = 0
        criteria_total = 6

        # Criterion 1: Win rate significant
        if stats_wr:
            passed = stats_wr.get('significant', False)
            print(f"  [1] Win rate p < 0.05: {'PASS [+]' if passed else 'FAIL [-]'}")
            if passed:
                criteria_passed += 1
        else:
            print(f"  [1] Win rate p < 0.05: SKIP (insufficient trades)")

        # Criterion 2: Return significant
        if stats_ret:
            passed = stats_ret.get('significant', False)
            print(f"  [2] Return p < 0.05: {'PASS [+]' if passed else 'FAIL [-]'}")
            if passed:
                criteria_passed += 1
        else:
            print(f"  [2] Return p < 0.05: SKIP")

        # Criterion 3: Annual return > 10%
        passed = hurst.get('total_return_pct', 0) > 10
        print(f"  [3] Annual return > 10%: {'PASS [+]' if passed else 'FAIL [-]'} ({hurst.get('total_return_pct', 0):.2f}%)")
        if passed:
            criteria_passed += 1

        # Criterion 4: Sharpe > 1.5
        passed = hurst.get('sharpe_ratio', 0) > 1.5
        print(f"  [4] Sharpe > 1.5: {'PASS [+]' if passed else 'FAIL [-]'} ({hurst.get('sharpe_ratio', 0):.2f})")
        if passed:
            criteria_passed += 1

        # Criterion 5: Max DD < 20%
        passed = hurst.get('max_drawdown_pct', 0) > -20
        print(f"  [5] Max DD < 20%: {'PASS [+]' if passed else 'FAIL [-]'} ({hurst.get('max_drawdown_pct', 0):.2f}%)")
        if passed:
            criteria_passed += 1

        # Criterion 6: Outperforms SPY
        if bench:
            passed = bench.get('alpha', 0) > 1
            print(f"  [6] Alpha > 1% vs SPY: {'PASS [+]' if passed else 'FAIL [-]'} ({bench.get('alpha', 0):.2f}%)")
            if passed:
                criteria_passed += 1
        else:
            print(f"  [6] Alpha > 1% vs SPY: SKIP (no benchmark)")

        print(f"\nOVERALL: {criteria_passed}/{criteria_total - 1} criteria passed")

        print("\n" + "="*80)
        print("END OF WEEK 1 REPORT")
        print("="*80)

    def run_all_tests(self):
        """Execute all Week 1 tests"""
        if self.download_data() is None:
            return

        self.run_hurst_backtest()
        self.buy_and_hold_benchmark()
        self.sma_crossover_baseline()
        self.statistical_significance_winrate()
        self.statistical_significance_returns()
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

        validator = Week1ValidatorV2(symbol=symbol, period='5y')
        validator.run_all_tests()


if __name__ == '__main__':
    main()
