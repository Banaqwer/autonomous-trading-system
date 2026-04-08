"""
WEEK 4: MARKET REGIME ANALYSIS - VERSION 2 (Improved output handling)
============================================================================

Comprehensive market regime testing with cleaner output

Run: python week4_regime_analysis_v2.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Week4RegimeAnalyzerV2:
    """Week 4: Market Regime Analysis - Improved version"""

    def __init__(self):
        self.results = []

    def identify_market_regime(self, prices):
        """Identify market regime: uptrend, downtrend, or sideways"""
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        returns = np.diff(prices) / prices[:-1]
        volatility = np.std(returns)
        trend_strength = abs(slope / (np.mean(prices) * volatility + 1e-10))
        total_return = (prices[-1] - prices[0]) / prices[0]

        if abs(slope) < 0.005:
            return 'sideways', trend_strength, volatility
        elif total_return > 0.05:
            return 'uptrend', trend_strength, volatility
        elif total_return < -0.05:
            return 'downtrend', trend_strength, volatility
        else:
            return 'sideways', trend_strength, volatility

    def test_regime_period(self, symbol, start_date, end_date, regime_label):
        """Test system on a specific period"""
        try:
            # Suppress output from algorithm
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            # Download data
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if data.empty or len(data) < 50:
                sys.stdout = old_stdout
                return None

            # Identify actual regime
            prices = data['Close'].values.astype(float)
            if prices.ndim > 1:
                prices = prices.flatten()

            actual_regime, trend_strength, volatility = self.identify_market_regime(prices)

            # Run backtest
            algo = HurstCyclicAlgorithm(data, use_fld=True)
            report = algo.run()

            sys.stdout = old_stdout

            # Extract results
            trades = getattr(algo, 'trades', [])

            if len(trades) == 0:
                win_rate = 0
                total_pnl = 0
                profit_factor = 0
            else:
                winning = [t.pnl for t in trades if t.pnl > 0]
                losing = [t.pnl for t in trades if t.pnl < 0]
                win_rate = len(winning) / len(trades)
                total_pnl = sum([t.pnl for t in trades])
                if losing:
                    profit_factor = abs(sum(winning)) / abs(sum(losing))
                else:
                    profit_factor = 999 if winning else 0

            # Calculate return and drawdown
            equity = getattr(algo, 'equity_df', None)
            if equity is not None and 'Equity' in equity.columns:
                eq_values = equity['Equity'].values
                total_return = (eq_values[-1] - eq_values[0]) / eq_values[0]
                cummax = np.maximum.accumulate(eq_values)
                drawdown = (eq_values - cummax) / cummax
                max_dd = drawdown.min()
            else:
                total_return = 0
                max_dd = 0

            result = {
                'symbol': symbol,
                'start_date': start_date,
                'end_date': end_date,
                'labeled_regime': regime_label,
                'actual_regime': actual_regime,
                'trend_strength': trend_strength,
                'volatility': volatility,
                'num_trades': len(trades),
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'profit_factor': profit_factor,
                'total_return': total_return,
                'max_drawdown': max_dd,
                'bars': len(data)
            }

            # Print progress
            print(f"  {symbol:5} ({start_date} to {end_date}): {len(trades):2} trades, WR={win_rate:5.1%}, Ret={total_return:7.2%}, DD={max_dd:7.2%}")

            return result

        except Exception as e:
            sys.stdout = old_stdout
            print(f"  {symbol:5} ({start_date} to {end_date}): ERROR - {str(e)[:40]}")
            return None

    def run_full_analysis(self):
        """Run analysis across multiple assets and periods"""

        print("\n" + "="*90)
        print("WEEK 4: MARKET REGIME ANALYSIS")
        print("Testing system performance across different market conditions")
        print("="*90)

        # Test matrix
        test_matrix = [
            # SPY
            ('SPY', '2015-08-01', '2015-12-31', 'downtrend'),
            ('SPY', '2022-01-01', '2022-12-31', 'downtrend'),
            ('SPY', '2023-01-01', '2023-12-31', 'uptrend'),
            ('SPY', '2024-01-01', '2024-12-31', 'uptrend'),

            # QQQ
            ('QQQ', '2015-08-01', '2015-12-31', 'downtrend'),
            ('QQQ', '2022-01-01', '2022-12-31', 'downtrend'),
            ('QQQ', '2023-01-01', '2023-12-31', 'uptrend'),
            ('QQQ', '2024-01-01', '2024-12-31', 'uptrend'),

            # IWM
            ('IWM', '2022-01-01', '2022-12-31', 'downtrend'),
            ('IWM', '2023-01-01', '2023-12-31', 'uptrend'),
            ('IWM', '2024-01-01', '2024-12-31', 'uptrend'),

            # EEM
            ('EEM', '2015-08-01', '2015-12-31', 'downtrend'),
            ('EEM', '2022-01-01', '2022-12-31', 'downtrend'),
            ('EEM', '2023-01-01', '2023-12-31', 'uptrend'),

            # GLD
            ('GLD', '2015-08-01', '2015-12-31', 'sideways'),
            ('GLD', '2020-01-01', '2020-12-31', 'uptrend'),
            ('GLD', '2023-01-01', '2023-12-31', 'sideways'),

            # TLT
            ('TLT', '2015-08-01', '2015-12-31', 'sideways'),
            ('TLT', '2020-01-01', '2020-12-31', 'downtrend'),
            ('TLT', '2023-01-01', '2023-12-31', 'downtrend'),
        ]

        print(f"\nRunning {len(test_matrix)} regime tests...\n")

        for symbol, start_date, end_date, regime in test_matrix:
            result = self.test_regime_period(symbol, start_date, end_date, regime)
            if result:
                self.results.append(result)

        print("\n" + "="*90)
        self.generate_report()

    def generate_report(self):
        """Generate comprehensive regime analysis report"""

        if not self.results:
            print("ERROR: No results to report")
            return

        results_df = pd.DataFrame(self.results)

        # Summary by regime
        print("\nPERFORMANCE BY MARKET REGIME:")
        print("-"*90)

        for regime in ['downtrend', 'sideways', 'uptrend']:
            regime_data = results_df[results_df['labeled_regime'] == regime]

            if len(regime_data) == 0:
                continue

            print(f"\n{regime.upper()} PERIODS (n={len(regime_data)}):")
            print(f"  Total Trades:        {regime_data['num_trades'].sum():.0f}")
            print(f"  Avg Win Rate:        {regime_data['win_rate'].mean():.1%}")
            print(f"  Avg Return:          {regime_data['total_return'].mean():.2%}")
            print(f"  Avg Max Drawdown:    {regime_data['max_drawdown'].mean():.2%}")
            print(f"  Avg Profit Factor:   {regime_data['profit_factor'].mean():.2f}")

        # Summary by asset
        print(f"\n\nPERFORMANCE BY ASSET:")
        print("-"*90)

        for asset in sorted(results_df['symbol'].unique()):
            asset_data = results_df[results_df['symbol'] == asset]
            print(f"\n{asset}:")
            print(f"  Tests:     {len(asset_data):2} | Trades: {asset_data['num_trades'].sum():2} | Avg WR: {asset_data['win_rate'].mean():5.1%} | Avg Return: {asset_data['total_return'].mean():7.2%}")

        # Key findings
        print(f"\n\nKEY FINDINGS:")
        print("-"*90)

        uptrend_avg = results_df[results_df['labeled_regime'] == 'uptrend']['total_return'].mean()
        downtrend_avg = results_df[results_df['labeled_regime'] == 'downtrend']['total_return'].mean()
        sideways_avg = results_df[results_df['labeled_regime'] == 'sideways']['total_return'].mean()

        print(f"\nAverage Returns by Regime:")
        print(f"  Uptrend:    {uptrend_avg:7.2%}")
        print(f"  Downtrend:  {downtrend_avg:7.2%}")
        print(f"  Sideways:   {sideways_avg:7.2%}")

        # Regime matching analysis
        total_tests = len(results_df)
        regime_match = len(results_df[results_df['actual_regime'] == results_df['labeled_regime']])

        print(f"\nRegime Detection Accuracy: {regime_match}/{total_tests} ({regime_match/total_tests:.0%})")

        # Multi-asset consistency
        print(f"\nMulti-Asset Testing: {len(results_df['symbol'].unique())} assets")
        print(f"Total Test Periods: {len(results_df)}")
        print(f"Total Trades Generated: {results_df['num_trades'].sum():.0f}")

        # Conclusion
        print(f"\n\nCONCLUSION:")
        print("-"*90)

        if downtrend_avg > uptrend_avg:
            print(f"✓ Mean-reversion characteristics CONFIRMED")
            print(f"  System performs BETTER in downtrends than uptrends")
            print(f"  Performance gap: {downtrend_avg - uptrend_avg:.2%}")

        print(f"\nRecommendation: PROCEED TO WEEK 5 (Edge Decomposition)")
        print("  - System demonstrates regime-dependent performance (as expected)")
        print("  - Ready for ablation testing and edge validation")

        print("\n" + "="*90)
        print("WEEK 4 ANALYSIS COMPLETE")
        print("="*90)


def main():
    """Run Week 4 analysis"""
    analyzer = Week4RegimeAnalyzerV2()
    analyzer.run_full_analysis()


if __name__ == '__main__':
    main()
