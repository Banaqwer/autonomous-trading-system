"""
WEEK 3: RISK ANALYSIS - COMPREHENSIVE RISK METRICS
============================================================================

Risk-adjusted performance validation:
1. Sharpe Ratio (risk-adjusted return)
2. Sortino Ratio (downside deviation)
3. Calmar Ratio (return / max drawdown)
4. Value at Risk (VaR) - 95% confidence
5. Conditional Value at Risk (CVaR) - expected shortfall
6. Downside Deviation
7. Information Ratio vs benchmark

Run: python week3_risk_analysis.py
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class Week3RiskAnalyzer:
    """Week 3: Risk Analysis & Metrics"""

    def __init__(self, symbol='SPY', start_date='2019-01-01', end_date='2024-12-31'):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.data = None
        self.algo = None

    def download_and_backtest(self):
        """Download data and run backtest"""
        print(f"\n[1] Downloading {self.symbol} ({self.start_date} to {self.end_date})...")

        self.data = yf.download(self.symbol, start=self.start_date,
                               end=self.end_date, progress=False)

        if self.data.empty:
            print("    ERROR: No data")
            return False

        print(f"    OK: {len(self.data)} bars")

        print(f"\n[2] Running backtest...")
        self.algo = HurstCyclicAlgorithm(self.data, use_fld=True)
        report = self.algo.run()

        trades = getattr(self.algo, 'trades', [])
        print(f"    OK: {len(trades)} trades executed")

        return True

    def calculate_risk_metrics(self):
        """Calculate comprehensive risk metrics"""
        print(f"\n[3] Calculating Risk Metrics...")

        metrics = {}

        # Get equity curve
        if not hasattr(self.algo, 'equity_df') or self.algo.equity_df is None:
            print("    ERROR: No equity curve")
            return metrics

        equity = self.algo.equity_df['Equity'].values

        if len(equity) < 2:
            print("    ERROR: Insufficient data")
            return metrics

        # Daily returns
        daily_returns = np.diff(equity) / equity[:-1]

        # Basic stats
        mean_return = daily_returns.mean()
        std_return = daily_returns.std()

        # Risk-free rate (annual)
        rf_annual = 0.045
        rf_daily = rf_annual / 252

        # === SHARPE RATIO ===
        if std_return > 0:
            sharpe = (mean_return - rf_daily) / std_return * np.sqrt(252)
        else:
            sharpe = 0
        metrics['sharpe_ratio'] = sharpe

        # === SORTINO RATIO ===
        # Only penalize downside volatility
        downside_returns = daily_returns[daily_returns < 0]
        if len(downside_returns) > 0:
            downside_std = downside_returns.std()
            if downside_std > 0:
                sortino = (mean_return - rf_daily) / downside_std * np.sqrt(252)
            else:
                sortino = 0
        else:
            sortino = 0
        metrics['sortino_ratio'] = sortino

        # === CALMAR RATIO ===
        # Return / Max Drawdown
        cummax = np.maximum.accumulate(equity)
        drawdown = (equity - cummax) / cummax
        max_dd = abs(drawdown.min())

        annual_return = (equity[-1] / equity[0] - 1)
        if max_dd > 0:
            calmar = annual_return / max_dd
        else:
            calmar = 0
        metrics['calmar_ratio'] = calmar

        # === VALUE AT RISK (VaR) ===
        # 95% confidence level (5% worst case)
        var_95 = np.percentile(daily_returns, 5)
        metrics['var_95'] = var_95

        # === CONDITIONAL VALUE AT RISK (CVaR) ===
        # Expected shortfall (average of worst 5%)
        cvar_returns = daily_returns[daily_returns <= var_95]
        if len(cvar_returns) > 0:
            cvar_95 = cvar_returns.mean()
        else:
            cvar_95 = var_95
        metrics['cvar_95'] = cvar_95

        # === DOWNSIDE DEVIATION ===
        downside_deviation = np.sqrt(np.mean(np.minimum(daily_returns - rf_daily, 0)**2))
        metrics['downside_deviation'] = downside_deviation

        # === RECOVERY FACTOR ===
        total_profit = equity[-1] - equity[0]
        max_loss = np.min(np.diff(equity))
        if max_loss < 0:
            recovery_factor = total_profit / abs(max_loss)
        else:
            recovery_factor = 0
        metrics['recovery_factor'] = recovery_factor

        # === PROFIT FACTOR ===
        winning_trades = [t.pnl for t in self.algo.trades if t.pnl > 0]
        losing_trades = [t.pnl for t in self.algo.trades if t.pnl < 0]

        if len(losing_trades) > 0:
            profit_factor = sum(winning_trades) / abs(sum(losing_trades))
        else:
            profit_factor = 0
        metrics['profit_factor'] = profit_factor

        # === MAXIMUM DRAWDOWN ===
        metrics['max_drawdown'] = drawdown.min()

        # === RETURN METRICS ===
        total_return = (equity[-1] / equity[0] - 1)
        metrics['total_return'] = total_return
        metrics['annual_return'] = total_return / ((len(self.data) - 1) / 252)

        return metrics

    def generate_report(self, metrics):
        """Generate risk analysis report"""
        print("\n" + "="*80)
        print(f"WEEK 3: RISK ANALYSIS REPORT - {self.symbol}")
        print("="*80)

        print(f"\nRISK-ADJUSTED RETURNS:")
        print(f"  Sharpe Ratio............ {metrics.get('sharpe_ratio', 0):.2f}")
        print(f"  Sortino Ratio.......... {metrics.get('sortino_ratio', 0):.2f}")
        print(f"  Calmar Ratio........... {metrics.get('calmar_ratio', 0):.2f}")

        print(f"\nTAIL RISK METRICS:")
        print(f"  Value at Risk (95%)..... {metrics.get('var_95', 0):.4f} ({metrics.get('var_95', 0)*100:.2f}%)")
        print(f"  CVaR (95%)............. {metrics.get('cvar_95', 0):.4f} ({metrics.get('cvar_95', 0)*100:.2f}%)")
        print(f"  Downside Deviation..... {metrics.get('downside_deviation', 0):.4f}")

        print(f"\nDRAWDOWN METRICS:")
        print(f"  Max Drawdown........... {metrics.get('max_drawdown', 0):.4f} ({metrics.get('max_drawdown', 0)*100:.2f}%)")
        print(f"  Recovery Factor........ {metrics.get('recovery_factor', 0):.2f}")

        print(f"\nPERFORMANCE METRICS:")
        print(f"  Total Return........... {metrics.get('total_return', 0):.4f} ({metrics.get('total_return', 0)*100:.2f}%)")
        print(f"  Annual Return.......... {metrics.get('annual_return', 0):.4f} ({metrics.get('annual_return', 0)*100:.2f}%)")
        print(f"  Profit Factor.......... {metrics.get('profit_factor', 0):.2f}")

        # Assessment
        print(f"\nRISK ASSESSMENT:")
        sharpe = metrics.get('sharpe_ratio', 0)
        max_dd = metrics.get('max_drawdown', 0)

        if sharpe > 1.5 and max_dd > -0.20:
            print(f"  [+] EXCELLENT: Strong risk-adjusted returns")
        elif sharpe > 1.0 and max_dd > -0.25:
            print(f"  [+] GOOD: Acceptable risk-adjusted returns")
        elif sharpe > 0.5 and max_dd > -0.30:
            print(f"  [~] MODERATE: Fair risk profile")
        else:
            print(f"  [-] CAUTION: Elevated risk")

        print("="*80)

    def run(self):
        """Run Week 3 analysis"""
        print("\n" + "="*80)
        print("WEEK 3: RISK ANALYSIS")
        print(f"Asset: {self.symbol}")
        print(f"Period: {self.start_date} to {self.end_date}")
        print("="*80)

        if not self.download_and_backtest():
            return

        metrics = self.calculate_risk_metrics()
        self.generate_report(metrics)


def main():
    """Run Week 3 for multiple periods"""
    print("\n" + "="*80)
    print("WEEK 3: COMPREHENSIVE RISK ANALYSIS")
    print("="*80)

    # Full period
    analyzer = Week3RiskAnalyzer('SPY', '2019-01-01', '2024-12-31')
    analyzer.run()

    print(f"\n\n{'='*80}")
    print("WEEK 3 RISK ANALYSIS COMPLETE")
    print("="*80)
    print("\nNext: Week 4 - Market Regime Analysis")


if __name__ == '__main__':
    main()
