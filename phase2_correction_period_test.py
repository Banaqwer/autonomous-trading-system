"""
PHASE 2: OPTION 2 - TEST ON 2022 CORRECTION PERIOD
============================================================================

The 2020-2022 period was fundamentally a RECOVERY (uptrend).
Testing on 2022 only: Pure CORRECTION/SIDEWAYS market (ideal for mean-reversion).

2022 Performance:
- SPY: -18.1% (correction)
- QQQ: -33.6% (correction)
- IWM: -19.6% (correction)

Market Regime: Perfect for cycle-based mean-reversion strategies
Expected: 5-15 signals, 50%+ win rate

Run: python phase2_correction_period_test.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
from scipy import stats
from scipy.stats import ttest_1samp
import warnings
warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class CorrectionPeriodValidator:
    """Test on 2022 correction period (ideal for mean-reversion)"""

    def __init__(self, symbol='SPY'):
        self.symbol = symbol
        self.data = None
        self.prices = None
        self.results = {}

    def download_data(self):
        """Download 2022 correction data"""
        print(f"\n[1] Downloading {self.symbol} (2022 CORRECTION PERIOD)...")
        try:
            self.data = yf.download(self.symbol, start='2022-01-01',
                                    end='2022-12-31', progress=False)
            if self.data.empty:
                print(f"    ERROR: No data")
                return False

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

            # Calculate metrics
            period_return = (prices[-1] / prices[0] - 1) * 100
            returns = np.diff(prices) / prices[:-1]
            annual_vol = returns.std() * np.sqrt(252) * 100

            print(f"    OK: {len(self.data)} bars")
            print(f"         Price: ${prices.min():.2f} - ${prices.max():.2f}")
            print(f"         Return: {period_return:+.2f}%")
            print(f"         Volatility: {annual_vol:.1f}% annualized")

            return True

        except Exception as e:
            print(f"    ERROR: {e}")
            return False

    def run_hurst_backtest(self):
        """Run Hurst system on correction period"""
        print(f"\n[2] Running Hurst Cyclic Trading (2022 Correction)...")
        print(f"         Confluence threshold: 0.20 (20%)")
        try:
            algo = HurstCyclicAlgorithm(self.data, use_fld=True,
                                       use_trigonometric_refinement=True)
            report = algo.run()

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

            trades = getattr(algo, 'trades', [])
            total_trades = len(trades)

            if total_trades > 0:
                winning_trades = len([t for t in trades if hasattr(t, 'pnl') and t.pnl > 0])
                losing_trades = len([t for t in trades if hasattr(t, 'pnl') and t.pnl < 0])
                win_rate = winning_trades / total_trades
                total_pnl = sum(t.pnl for t in trades if hasattr(t, 'pnl'))
                total_return_pct = (total_pnl / 100000) * 100

                if hasattr(algo, 'equity_df') and algo.equity_df is not None and len(algo.equity_df) > 1:
                    try:
                        daily_returns = algo.equity_df['Daily Return'].dropna()
                        if len(daily_returns) > 0 and daily_returns.std() > 0:
                            sharpe_ratio = (daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252))
                        else:
                            sharpe_ratio = 0
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

            if total_trades == 0:
                print(f"\n    [!] No trades in 2022 correction period")
                print(f"    [!] This suggests the strategy may not be suited to 2022 dynamics")
                print(f"    [!] Next: Try 2015-2016 period or earlier correction")
                return False

            return True

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

    def statistical_tests(self):
        """Statistical tests"""
        print(f"\n[3] Statistical Tests...")

        hurst = self.results.get('hurst', {})
        trades = hurst.get('total_trades', 0)
        wins = hurst.get('winning_trades', 0)

        if trades >= 3:
            try:
                result = stats.binomtest(wins, trades, 0.5, alternative='two-sided')
                p_value = result.pvalue
                sig = "PASS [+]" if p_value < 0.05 else "FAIL [-]"
                print(f"    Binomial test: p={p_value:.4f} {sig}")
                self.results['binomial_pvalue'] = p_value
            except AttributeError:
                p_value = stats.binom_test(wins, trades, 0.5)
                sig = "PASS [+]" if p_value < 0.05 else "FAIL [-]"
                print(f"    Binomial test: p={p_value:.4f} {sig}")
                self.results['binomial_pvalue'] = p_value
        else:
            print(f"    Binomial: SKIP (need >= 3 trades, have {trades})")

    def generate_report(self):
        """Generate report"""
        print("\n" + "="*80)
        print(f"2022 CORRECTION PERIOD TEST - {self.symbol}")
        print("="*80)

        hurst = self.results.get('hurst', {})

        print(f"\nRESULTS:")
        print(f"  Trades: {hurst.get('total_trades', 0)}")
        print(f"  Win Rate: {hurst.get('win_rate', 0):.1%}")
        print(f"  Return: {hurst.get('total_return_pct', 0):.2f}%")
        print(f"  Sharpe: {hurst.get('sharpe_ratio', 0):.2f}")
        print(f"  Max DD: {hurst.get('max_drawdown_pct', 0):.2f}%")

        print(f"\nEXPECTATIONS VS RESULTS:")
        trades_ok = hurst.get('total_trades', 0) >= 3
        wr_ok = hurst.get('win_rate', 0) >= 0.50
        return_ok = hurst.get('total_return_pct', 0) >= 5

        print(f"  [1] Generated 3+ trades: {'PASS [+]' if trades_ok else 'FAIL [-]'}")
        print(f"  [2] Win rate >= 50%: {'PASS [+]' if wr_ok else 'FAIL [-]'}")
        print(f"  [3] Return >= 5%: {'PASS [+]' if return_ok else 'FAIL [-]'}")

        criteria_met = sum([trades_ok, wr_ok, return_ok])

        if criteria_met >= 2:
            print(f"\n[+] SUCCESS: 2022 correction period generates signals!")
            print(f"    Parameters are correct for mean-reversion conditions")
        else:
            print(f"\n[-] 2022 also shows no signals")
            print(f"    Problem likely deeper - recommend debug approach")

        print("="*80)

    def run(self):
        """Run all tests"""
        print("\n" + "="*80)
        print(f"OPTION 2: TEST ON 2022 CORRECTION PERIOD")
        print(f"Asset: {self.symbol}")
        print(f"Market Regime: CORRECTION (ideal for mean-reversion)")
        print("="*80)

        if not self.download_data():
            return

        has_trades = self.run_hurst_backtest()
        if has_trades:
            self.statistical_tests()

        self.generate_report()


def main():
    """Test all assets on 2022 correction"""
    print("\n" + "="*80)
    print("PHASE 2 OPTION 2: 2022 CORRECTION PERIOD VALIDATION")
    print("="*80)

    for symbol in ['SPY', 'QQQ', 'IWM']:
        print(f"\n\n{'#'*80}")
        print(f"{symbol}")
        print(f"{'#'*80}")

        validator = CorrectionPeriodValidator(symbol=symbol)
        validator.run()

    print(f"\n\n{'='*80}")
    print("2022 CORRECTION TEST COMPLETE")
    print("="*80)


if __name__ == '__main__':
    main()
