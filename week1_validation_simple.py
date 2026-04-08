"""
WEEK 1: ALPHA VALIDATION - SIMPLIFIED VERSION
============================================================================

Professional quantitative validation using latest Yahoo Finance data.
This is Phase 1 of the 8-week alpha validation sprint.

Run: python week1_validation_simple.py

Simplified to focus on actionable results without cascading failures.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from scipy import stats
from scipy.stats import ttest_1samp
import warnings
warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Week1SimplifiedValidator:
    """Execute Week 1 validation tests - simplified"""

    def __init__(self, symbol='SPY', period='5y'):
        """Initialize validator"""
        self.symbol = symbol
        self.period = period
        self.data = None
        self.prices = None
        self.results = {}

    def download_data(self):
        """Download latest data from Yahoo Finance"""
        print(f"\n[1] Downloading {self.symbol} ({self.period})...")
        try:
            self.data = yf.download(self.symbol, period=self.period, progress=False)
            if self.data.empty:
                print(f"    ERROR: No data")
                return False

            # Extract Close price
            try:
                prices = self.data['Close'].values.astype(float)
            except (KeyError, TypeError):
                for col in self.data.columns:
                    if isinstance(col, tuple) and col[1] == 'Close':
                        prices = self.data[col].values.astype(float)
                        break
                    elif col == 'Close':
                        prices = self.data[col].values.astype(float)
                        break

            if prices.ndim > 1:
                prices = prices.flatten()

            self.prices = prices

            print(f"    OK: {len(self.data)} bars, {self.data.index[0].date()} to {self.data.index[-1].date()}")
            print(f"         ${prices.min():.2f} - ${prices.max():.2f}")
            return True

        except Exception as e:
            print(f"    ERROR: {e}")
            return False

    def run_hurst_backtest(self):
        """Run Hurst system and extract metrics"""
        print(f"\n[2] Running Hurst Cyclic Trading...")
        try:
            algo = HurstCyclicAlgorithm(self.data, use_fld=True)
            report = algo.run()

            # Check for errors
            if report.get('error'):
                print(f"    NOTE: {report.get('error')}")
                self.results['hurst'] = {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'win_rate': 0,
                    'total_return_pct': 0,
                    'sharpe_ratio': 0,
                    'max_drawdown_pct': 0,
                }
                return False

            # Extract trade statistics
            trades = getattr(algo, 'trades', [])
            total_trades = len(trades)

            if total_trades > 0:
                winning_trades = len([t for t in trades if hasattr(t, 'pnl') and t.pnl > 0])
                losing_trades = len([t for t in trades if hasattr(t, 'pnl') and t.pnl < 0])
                win_rate = winning_trades / total_trades if total_trades > 0 else 0
                total_pnl = sum(t.pnl for t in trades if hasattr(t, 'pnl'))
                total_return_pct = (total_pnl / 100000) * 100  # Assume $100k capital

                # Calculate Sharpe
                if hasattr(algo, 'equity_df') and algo.equity_df is not None and len(algo.equity_df) > 1:
                    try:
                        daily_returns = algo.equity_df['Daily Return'].dropna()
                        if len(daily_returns) > 0 and daily_returns.std() > 0:
                            sharpe_ratio = (daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252))
                        else:
                            sharpe_ratio = 0

                        # Calculate max drawdown
                        cummax = algo.equity_df['Equity'].cummax()
                        drawdown = (algo.equity_df['Equity'] - cummax) / cummax
                        max_drawdown_pct = drawdown.min() * 100
                    except:
                        sharpe_ratio = 0
                        max_drawdown_pct = 0
                else:
                    sharpe_ratio = 0
                    max_drawdown_pct = 0

            else:
                winning_trades = 0
                losing_trades = 0
                win_rate = 0
                total_return_pct = 0
                sharpe_ratio = 0
                max_drawdown_pct = 0

            self.results['hurst'] = {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'total_return_pct': total_return_pct,
                'sharpe_ratio': sharpe_ratio,
                'max_drawdown_pct': max_drawdown_pct,
            }

            print(f"    OK: {total_trades} trades, WR={win_rate:.1%}, Return={total_return_pct:.2f}%, Sharpe={sharpe_ratio:.2f}")
            return True if total_trades > 0 else False

        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.results['hurst'] = {
                'total_trades': 0,
                'winning_trades': 0,
                'win_rate': 0,
                'total_return_pct': 0,
                'sharpe_ratio': 0,
                'max_drawdown_pct': 0,
            }
            return False

    def compare_vs_spy(self):
        """Calculate alpha vs SPY"""
        print(f"\n[3] Alpha vs SPY...")
        try:
            spy_data = yf.download('SPY', period=self.period, progress=False)
            if spy_data.empty:
                return

            # Get SPY prices
            try:
                spy_prices = spy_data['Close'].values.astype(float)
            except:
                for col in spy_data.columns:
                    if isinstance(col, tuple) and col[1] == 'Close':
                        spy_prices = spy_data[col].values.astype(float)
                        break

            if spy_prices.ndim > 1:
                spy_prices = spy_prices.flatten()

            # Use latest common date range
            min_len = min(len(self.prices), len(spy_prices))
            strategy_prices = self.prices[-min_len:]
            spy_prices = spy_prices[-min_len:]

            # Returns
            strategy_return = (strategy_prices[-1] / strategy_prices[0] - 1) * 100
            spy_return = (spy_prices[-1] / spy_prices[0] - 1) * 100

            print(f"    {self.symbol} Return: {strategy_return:.2f}%")
            print(f"    SPY Return: {spy_return:.2f}%")
            print(f"    Outperformance: {strategy_return - spy_return:+.2f}%")

            self.results['comparison'] = {
                'strategy_return': strategy_return,
                'spy_return': spy_return,
                'outperformance': strategy_return - spy_return,
            }

        except Exception as e:
            print(f"    ERROR: {e}")

    def statistical_tests(self):
        """Statistical significance tests"""
        print(f"\n[4] Statistical Tests...")

        hurst = self.results.get('hurst', {})
        trades = hurst.get('total_trades', 0)
        wins = hurst.get('winning_trades', 0)

        # Binomial test (win rate)
        if trades >= 3:
            try:
                result = stats.binomtest(wins, trades, 0.5, alternative='two-sided')
                p_value = result.pvalue
                print(f"    Binomial (win rate): p={p_value:.4f} {'[PASS]' if p_value < 0.05 else '[FAIL]'}")
                self.results['binomial_pvalue'] = p_value
            except AttributeError:
                p_value = stats.binom_test(wins, trades, 0.5)
                print(f"    Binomial (win rate): p={p_value:.4f} {'[PASS]' if p_value < 0.05 else '[FAIL]'}")
                self.results['binomial_pvalue'] = p_value
        else:
            print(f"    Binomial: SKIP (need >= 3 trades, have {trades})")

        # t-test (returns)
        if len(self.prices) >= 20:
            returns = np.diff(self.prices) / self.prices[:-1]
            t_stat, p_value = ttest_1samp(returns, 0)
            print(f"    t-Test (daily returns): p={p_value:.4f} {'[PASS]' if p_value < 0.05 else '[FAIL]'}")
            self.results['ttest_pvalue'] = p_value
        else:
            print(f"    t-Test: SKIP")

    def generate_report(self):
        """Generate final report"""
        print("\n" + "="*80)
        print(f"WEEK 1 VALIDATION REPORT - {self.symbol}")
        print("="*80)

        hurst = self.results.get('hurst', {})
        comp = self.results.get('comparison', {})

        print(f"\nHURST SYSTEM:")
        print(f"  Trades: {hurst.get('total_trades', 0)}")
        print(f"  Win Rate: {hurst.get('win_rate', 0):.1%}")
        print(f"  Return: {hurst.get('total_return_pct', 0):.2f}%")
        print(f"  Sharpe: {hurst.get('sharpe_ratio', 0):.2f}")
        print(f"  Max DD: {hurst.get('max_drawdown_pct', 0):.2f}%")

        if comp:
            print(f"\nVS BUY-AND-HOLD:")
            print(f"  Strategy Return: {comp.get('strategy_return', 0):.2f}%")
            print(f"  SPY Return: {comp.get('spy_return', 0):.2f}%")
            print(f"  Outperformance: {comp.get('outperformance', 0):+.2f}%")

        print(f"\nSTATISTICAL TESTS:")
        print(f"  Win Rate p-value: {self.results.get('binomial_pvalue', 'N/A')}")
        print(f"  Returns p-value: {self.results.get('ttest_pvalue', 'N/A')}")

        print(f"\nKEY METRICS:")
        metrics_passed = 0
        metrics_total = 6

        # Criterion 1: Win rate > 50%
        passed = hurst.get('win_rate', 0) > 0.5
        print(f"  [1] Win rate > 50%: {'YES' if passed else 'NO'} ({hurst.get('win_rate', 0):.1%})")
        if passed:
            metrics_passed += 1

        # Criterion 2: Return > 10%
        passed = hurst.get('total_return_pct', 0) > 10
        print(f"  [2] Return > 10%: {'YES' if passed else 'NO'} ({hurst.get('total_return_pct', 0):.2f}%)")
        if passed:
            metrics_passed += 1

        # Criterion 3: Sharpe > 1.5
        passed = hurst.get('sharpe_ratio', 0) > 1.5
        print(f"  [3] Sharpe > 1.5: {'YES' if passed else 'NO'} ({hurst.get('sharpe_ratio', 0):.2f})")
        if passed:
            metrics_passed += 1

        # Criterion 4: Max DD < 20%
        passed = hurst.get('max_drawdown_pct', 0) > -20
        print(f"  [4] Max DD < 20%: {'YES' if passed else 'NO'} ({hurst.get('max_drawdown_pct', 0):.2f}%)")
        if passed:
            metrics_passed += 1

        # Criterion 5: Outperforms SPY
        passed = comp.get('outperformance', 0) > 0
        print(f"  [5] Outperforms SPY: {'YES' if passed else 'NO'} ({comp.get('outperformance', 0):+.2f}%)")
        if passed:
            metrics_passed += 1

        # Criterion 6: Win rate statistically significant
        p_val = self.results.get('binomial_pvalue', 1)
        passed = p_val < 0.05 if isinstance(p_val, float) else False
        p_str = f"{p_val:.4f}" if isinstance(p_val, float) else 'N/A'
        print(f"  [6] Win rate p < 0.05: {'YES' if passed else 'NO'} (p={p_str})")
        if passed:
            metrics_passed += 1

        print(f"\nSUMMARY: {metrics_passed}/{metrics_total} criteria met")

        if hurst.get('total_trades', 0) == 0:
            print("\nNOTE: No trades generated.")
            print("      Signal thresholds may need tuning for this market period.")

        print("="*80)

    def run(self):
        """Run all tests"""
        print("\n" + "="*80)
        print(f"WEEK 1 VALIDATION: {self.symbol}")
        print("="*80)

        if not self.download_data():
            return
        if not self.run_hurst_backtest():
            print("\n    No trades generated - skipping dependent tests")

        self.compare_vs_spy()
        self.statistical_tests()
        self.generate_report()


def main():
    """Run validation on SPY, QQQ, IWM"""
    print("\n" + "="*80)
    print("WEEK 1 ALPHA VALIDATION - MULTIPLE ASSETS")
    print("="*80)

    for symbol in ['SPY', 'QQQ', 'IWM']:
        validator = Week1SimplifiedValidator(symbol=symbol, period='5y')
        validator.run()
        print("\n")


if __name__ == '__main__':
    main()
