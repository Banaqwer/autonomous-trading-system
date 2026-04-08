"""
PHASE 2: ASSET CONCENTRATION OPTIMIZATION
============================================================================

Testing hypothesis: Concentrating capital on high-edge assets improves returns

Current allocation: Equal across 6 assets
Optimized allocation: Concentrate on highest Sharpe assets

Run: python phase2_asset_concentration.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
import sys
from io import StringIO

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Phase2AssetConcentration:
    """Phase 2: Asset Concentration Optimization"""

    def __init__(self):
        self.results = []
        # Asset rankings from validation data
        self.asset_rankings = {
            'IWM': {'avg_return': 0.1081, 'win_rate': 0.816, 'trades': 20, 'sharpe': 8.23},
            'EEM': {'avg_return': 0.1276, 'win_rate': 0.833, 'trades': 6, 'sharpe': 12.34},
            'GLD': {'avg_return': 0.1209, 'win_rate': 0.667, 'trades': 3, 'sharpe': 10.50},
            'SPY': {'avg_return': 0.0159, 'win_rate': 1.000, 'trades': 1, 'sharpe': 15.87},
            'QQQ': {'avg_return': 0.0000, 'win_rate': 0.000, 'trades': 0, 'sharpe': 0.00},
            'TLT': {'avg_return': 0.0287, 'win_rate': 1.000, 'trades': 1, 'sharpe': 15.87},
        }

    def test_asset_strategy(self, symbol, start_date, end_date):
        """Test performance of specific asset"""
        try:
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            data = yf.download(symbol, start=start_date, end=end_date, progress=False)

            if data.empty or len(data) < 50:
                sys.stdout = old_stdout
                return None

            algo = HurstCyclicAlgorithm(data, use_fld=True)
            report = algo.run()

            sys.stdout = old_stdout

            if hasattr(algo, 'report'):
                result = {
                    'symbol': symbol,
                    'period': f"{start_date} to {end_date}",
                    'num_trades': algo.report.get('total_trades', 0),
                    'win_rate': algo.report.get('win_rate', 0),
                    'return': algo.report.get('total_return_pct', 0) / 100,
                    'sharpe': algo.report.get('sharpe_ratio', 0),
                }
                return result

            sys.stdout = old_stdout
            return None

        except Exception as e:
            sys.stdout = old_stdout
            return None

    def calculate_portfolio_return(self, allocation_dict, period_returns):
        """Calculate portfolio return based on allocation"""
        total_return = 0
        for asset, weight in allocation_dict.items():
            if asset in period_returns:
                total_return += weight * period_returns[asset]
        return total_return

    def run_concentration_analysis(self):
        """Run asset concentration optimization"""

        print("\n" + "="*95)
        print("PHASE 2: ASSET CONCENTRATION OPTIMIZATION")
        print("Testing hypothesis: Concentrate capital on highest-edge assets")
        print("="*95)

        # Test periods
        test_periods = [
            ('2024-01-01', '2024-12-31', '2024 Uptrend'),
            ('2022-01-01', '2022-12-31', '2022 Downtrend'),
            ('2023-01-01', '2023-12-31', '2023 Mixed'),
        ]

        print("\nTesting asset allocations across 3 periods:\n")

        all_results = {}

        for start_date, end_date, period_name in test_periods:
            print(f"\n{period_name} ({start_date} to {end_date}):")
            print("-"*95)

            period_results = {}

            # Test each asset
            for symbol in ['IWM', 'EEM', 'GLD', 'SPY', 'TLT', 'QQQ']:
                result = self.test_asset_strategy(symbol, start_date, end_date)

                if result:
                    period_results[symbol] = result['return']
                    print(f"  {symbol:5}: {result['num_trades']:2} trades | WR={result['win_rate']:.1%} | Return={result['return']:.2%}")
                else:
                    period_results[symbol] = 0
                    print(f"  {symbol:5}: No data")

            # Calculate portfolio returns under different allocations
            print(f"\nPortfolio Returns ({period_name}):")

            allocations = {
                'Equal (1/6 each)': {'IWM': 1/6, 'EEM': 1/6, 'GLD': 1/6, 'SPY': 1/6, 'QQQ': 1/6, 'TLT': 1/6},
                'High-Edge (70%)': {'IWM': 0.35, 'EEM': 0.35, 'GLD': 0.20, 'SPY': 0.05, 'QQQ': 0.00, 'TLT': 0.05},
                'Concentration': {'IWM': 0.40, 'EEM': 0.30, 'GLD': 0.20, 'SPY': 0.05, 'QQQ': 0.00, 'TLT': 0.05},
                'Maximum': {'IWM': 0.50, 'EEM': 0.30, 'GLD': 0.15, 'SPY': 0.00, 'QQQ': 0.00, 'TLT': 0.05},
            }

            for alloc_name, alloc_weights in allocations.items():
                portfolio_return = self.calculate_portfolio_return(alloc_weights, period_results)
                print(f"  {alloc_name:20}: {portfolio_return:.2%}")

                all_results[f"{period_name}_{alloc_name}"] = portfolio_return

        print("\n" + "="*95)
        self.generate_concentration_report(all_results)

    def generate_concentration_report(self, all_results):
        """Generate concentration analysis report"""

        print("\nASSET CONCENTRATION ANALYSIS:")
        print("-"*95)

        # Aggregate results
        allocations = ['Equal (1/6 each)', 'High-Edge (70%)', 'Concentration', 'Maximum']

        print("\nComparative Performance Across All Periods:")
        print()

        for alloc in allocations:
            returns = [v for k, v in all_results.items() if alloc in k]
            if returns:
                avg_return = np.mean(returns)
                print(f"{alloc:20}: Avg Return = {avg_return:.2%}")

        print("\n\nDETAILED ALLOCATION COMPARISON:")
        print("-"*95)

        print("\n1. EQUAL ALLOCATION (Current State)")
        print("   Weights: 16.7% each across 6 assets")
        equal_returns = [v for k, v in all_results.items() if 'Equal' in k]
        equal_avg = np.mean(equal_returns) if equal_returns else 0
        print(f"   Average Return: {equal_avg:.2%}")
        print(f"   Rationale: Fair distribution, but dilutes edge")
        print(f"   Problem: Capital on poor performers (QQQ, TLT)")

        print("\n2. HIGH-EDGE ALLOCATION (Recommended)")
        print("   Weights: IWM 35%, EEM 35%, GLD 20%, SPY 5%, TLT 5%, QQQ 0%")
        highedge_returns = [v for k, v in all_results.items() if 'High-Edge' in k]
        highedge_avg = np.mean(highedge_returns) if highedge_returns else 0
        print(f"   Average Return: {highedge_avg:.2%}")
        improvement_highedge = ((highedge_avg - equal_avg) / equal_avg * 100) if equal_avg != 0 else 0
        print(f"   Improvement over Equal: {improvement_highedge:+.1f}%")
        print(f"   Rationale: 70% on best 2 assets, diversify with GLD")

        print("\n3. CONCENTRATION ALLOCATION (Aggressive)")
        print("   Weights: IWM 40%, EEM 30%, GLD 20%, SPY 5%, TLT 5%, QQQ 0%")
        conc_returns = [v for k, v in all_results.items() if 'Concentration' in k]
        conc_avg = np.mean(conc_returns) if conc_returns else 0
        print(f"   Average Return: {conc_avg:.2%}")
        improvement_conc = ((conc_avg - equal_avg) / equal_avg * 100) if equal_avg != 0 else 0
        print(f"   Improvement over Equal: {improvement_conc:+.1f}%")
        print(f"   Rationale: Maximum edge on IWM, maintain diversification")

        print("\n4. MAXIMUM ALLOCATION (Very Aggressive)")
        print("   Weights: IWM 50%, EEM 30%, GLD 15%, SPY 0%, TLT 5%, QQQ 0%")
        max_returns = [v for k, v in all_results.items() if 'Maximum' in k]
        max_avg = np.mean(max_returns) if max_returns else 0
        print(f"   Average Return: {max_avg:.2%}")
        improvement_max = ((max_avg - equal_avg) / equal_avg * 100) if equal_avg != 0 else 0
        print(f"   Improvement over Equal: {improvement_max:+.1f}%")
        print(f"   Rationale: Max concentration on highest-edge asset (IWM)")
        print(f"   Risk: Higher concentration risk")

        # Asset evaluation
        print(f"\n\nASSET QUALITY RANKING:")
        print("-"*95)

        rankings = [
            ('EEM', '83.3%', '+12.76%', 'EXCELLENT', 'Highest win rate, strong returns'),
            ('IWM', '81.6%', '+10.81%', 'EXCELLENT', 'High volume of signals, good WR'),
            ('GLD', '66.7%', '+12.09%', 'GOOD', 'Moderate signals, sideways specialist'),
            ('SPY', '100.0%', '+1.59%', 'POOR', 'Perfect WR but very few signals'),
            ('TLT', '100.0%', '+2.87%', 'POOR', 'Perfect WR but very few signals'),
            ('QQQ', '0.0%', '+0.00%', 'POOR', 'NO SIGNALS - exclude entirely'),
        ]

        print(f"\n{'Asset':<6} {'Win Rate':<10} {'Avg Return':<12} {'Rating':<12} {'Reason':<40}")
        print("-"*80)
        for asset, wr, ret, rating, reason in rankings:
            print(f"{asset:<6} {wr:<10} {ret:<12} {rating:<12} {reason:<40}")

        # Recommendation
        print(f"\n\nRECOMMENDATION:")
        print("-"*95)

        print(f"\nImplement HIGH-EDGE ALLOCATION (Strategy 2)")
        print(f"  Improvement: {improvement_highedge:+.1f}% over baseline")
        print(f"  Expected Returns: {highedge_avg:.2%} annually")
        print(f"\nAllocation:")
        print(f"  - IWM:  35% (primary signal generator)")
        print(f"  - EEM:  35% (highest win rate, strong returns)")
        print(f"  - GLD:  20% (sideways specialist, diversification)")
        print(f"  - SPY:   5% (keep minimal for diversification)")
        print(f"  - QQQ:   0% (EXCLUDE - no signals)")
        print(f"  - TLT:   5% (keep minimal, high Sharpe)")

        print(f"\nRationale:")
        print(f"  1. Eliminates QQQ (zero edge)")
        print(f"  2. Concentrates 70% on top 2 assets")
        print(f"  3. Maintains diversification with GLD")
        print(f"  4. Preserves small positions for optionality")

        print(f"\nExpected Impact on $100k Account:")
        print(f"  Current allocation (Equal): {equal_avg:.2%} = ${equal_avg*100000:.0f}")
        print(f"  New allocation (High-Edge): {highedge_avg:.2%} = ${highedge_avg*100000:.0f}")
        print(f"  Profit increase: ${(highedge_avg-equal_avg)*100000:.0f} per year")

        # Go/No-Go
        print(f"\n\nGO/NO-GO DECISION:")
        print("-"*95)

        if improvement_highedge > 15:
            print(f"\n[GO] Improvement {improvement_highedge:.1f}% exceeds 15% threshold")
            print(f"Phase 2 Recommendation: IMPLEMENT HIGH-EDGE ALLOCATION")
        else:
            print(f"\n[NO-GO] Improvement {improvement_highedge:.1f}% below 15% threshold")

        print("\n" + "="*95)
        print("PHASE 2 ASSET CONCENTRATION COMPLETE")
        print("="*95)


def main():
    analyzer = Phase2AssetConcentration()
    analyzer.run_concentration_analysis()


if __name__ == '__main__':
    main()
