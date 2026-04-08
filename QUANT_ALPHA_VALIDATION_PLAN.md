# HURST CYCLIC TRADING - QUANTITATIVE ALPHA VALIDATION PLAN
## Professional Quant Development Roadmap

**Objective:** Find and prove the strategy's alpha (risk-adjusted outperformance) using institutional-grade statistical methods.

**Timeline:** 8 weeks of comprehensive testing
**Difficulty:** Advanced (requires statistical knowledge)

---

## PHASE 1: BASELINE ESTABLISHMENT (Week 1)
### What: Establish what the strategy is beating

#### 1.1 Buy-and-Hold Benchmark
```
Purpose: Prove we beat simple passive investing
Method: Compare returns against:
  - S&P 500 (SPY)
  - Nasdaq (QQQ)
  - Equal-weighted daily rebalance
  - Random walk (synthetic)

Metric:
  Alpha = Strategy Return - (Risk-Free Rate + Beta × Market Return)
  Expected: Alpha > 100 bps/year (1% outperformance)
```

**Implementation:**
```python
# Calculate beta
returns_strategy = (equity_curve.pct_change())[1:]
returns_spy = spy.pct_change()[1:]
covariance = np.cov(returns_strategy, returns_spy)[0][1]
variance_spy = np.var(returns_spy)
beta = covariance / variance_spy

# Calculate alpha
risk_free_rate = 0.045  # Current 10Y treasury
market_excess_return = returns_spy.mean() * 252 - risk_free_rate
alpha = (returns_strategy.mean() * 252 - risk_free_rate) - (beta * market_excess_return)
```

#### 1.2 Random Entry Baseline
```
Purpose: Prove we beat pure randomness
Method: Generate random entry signals with same entry/exit mechanics
  - Same position sizing
  - Same stop/target distances
  - Same risk management
  - Only difference: random timing

Metric:
  Strategy Win Rate vs Random Win Rate
  Expected: Strategy > Random by 10%+ (55% vs 45%)
```

**Implementation:**
```python
# Generate random signals on same dates as real signals
np.random.seed(42)
random_bars = np.random.choice(len(prices), size=len(real_signals), replace=False)
random_signals = [Signal(bar=b, ...) for b in sorted(random_bars)]

# Backtest random signals with same mechanics
random_report = backtest(random_signals)
print(f"Strategy WR: {strategy_wr:.1%}")
print(f"Random WR: {random_report['win_rate']:.1%}")
print(f"Edge: {strategy_wr - random_report['win_rate']:.1%}")
```

#### 1.3 Simple Moving Average Baseline
```
Purpose: Prove we beat basic technical analysis
Method: Simple moving average crossover (50/200)
  - Buy when 50MA > 200MA
  - Sell when 50MA < 200MA
  - Same position sizing and stops

Metric:
  Sharpe Ratio Comparison
  Expected: Strategy Sharpe > SMA Sharpe by 0.5+
```

**Implementation:**
```python
# SMA strategy
ma50 = talib.SMA(prices, 50)
ma200 = talib.SMA(prices, 200)
sma_signals = []
for i in range(200, len(prices)):
    if ma50[i] > ma200[i] and ma50[i-1] <= ma200[i-1]:
        sma_signals.append(Signal(bar=i, side=LONG, ...))
    elif ma50[i] < ma200[i] and ma50[i-1] >= ma200[i-1]:
        sma_signals.append(Signal(bar=i, side=SHORT, ...))

sma_report = backtest(sma_signals)
```

---

## PHASE 2: STATISTICAL SIGNIFICANCE (Week 1-2)
### What: Prove results are NOT due to luck

#### 2.1 Hypothesis Testing - Win Rate
```
Null Hypothesis: Win rate = 50% (coin flip)
Test: Binomial test

Result: If p-value < 0.05, win rate is statistically significant
```

**Implementation:**
```python
from scipy.stats import binom_test

trades = 47
wins = 28
p_value = binom_test(wins, trades, 0.5)

print(f"Trades: {trades}")
print(f"Win Rate: {wins/trades:.1%}")
print(f"P-value: {p_value:.6f}")
print(f"Significant: {'YES' if p_value < 0.05 else 'NO'}")

# Expected for 47 trades, 60% WR: p-value = 0.0003 (highly significant)
```

#### 2.2 Hypothesis Testing - Returns
```
Null Hypothesis: Strategy returns = 0 (no edge)
Test: t-test on monthly/weekly returns

Result: If p-value < 0.05, returns are significantly positive
```

**Implementation:**
```python
from scipy.stats import ttest_1samp

# Get monthly returns
monthly_returns = equity_curve.resample('M').last().pct_change()[1:]

# One-sample t-test vs 0
t_stat, p_value = ttest_1samp(monthly_returns, 0)

print(f"Monthly Returns Mean: {monthly_returns.mean():.2%}")
print(f"Monthly Returns Std: {monthly_returns.std():.2%}")
print(f"t-statistic: {t_stat:.4f}")
print(f"p-value: {p_value:.6f}")
print(f"Significant: {'YES' if p_value < 0.05 else 'NO'}")
```

#### 2.3 Confidence Intervals
```
Calculate 95% confidence intervals around key metrics:
  - Win rate (binomial proportion CI)
  - Average return (t-distribution CI)
  - Sharpe ratio (bootstrap CI)

Result: If CI doesn't include benchmark, alpha is real
```

**Implementation:**
```python
from scipy import stats

# Sharpe ratio bootstrap CI
def bootstrap_sharpe(returns, n_bootstrap=1000):
    sharpes = []
    for _ in range(n_bootstrap):
        sample = np.random.choice(returns, size=len(returns), replace=True)
        sharpes.append(sample.mean() / sample.std() * np.sqrt(252))
    return np.percentile(sharpes, [2.5, 97.5])

ci_lower, ci_upper = bootstrap_sharpe(daily_returns)
print(f"Sharpe 95% CI: [{ci_lower:.2f}, {ci_upper:.2f}]")
print(f"Benchmark Sharpe: 1.0")
print(f"Alpha Proven: {'YES' if ci_lower > 1.0 else 'NO'}")
```

#### 2.4 Expectancy Confidence Interval
```
Expectancy = (Win% × Avg Win) - (Loss% × Avg Loss)

Calculate 95% CI around expectancy
Result: If CI > 0, edge is real
```

**Implementation:**
```python
winners = [t.pnl for t in trades if t.pnl > 0]
losers = [t.pnl for t in trades if t.pnl < 0]

win_pct = len(winners) / len(trades)
avg_win = np.mean(winners) if winners else 0
avg_loss = abs(np.mean(losers)) if losers else 0

expectancy = (win_pct * avg_win) - ((1-win_pct) * avg_loss)

# Bootstrap expectancy CI
expectancies = []
for _ in range(1000):
    sample_trades = np.random.choice(trades, size=len(trades), replace=True)
    w = sum(1 for t in sample_trades if t.pnl > 0)
    aw = np.mean([t.pnl for t in sample_trades if t.pnl > 0])
    al = abs(np.mean([t.pnl for t in sample_trades if t.pnl < 0]))
    exp = (w/len(trades) * aw) - ((1-w/len(trades)) * al)
    expectancies.append(exp)

ci_lower, ci_upper = np.percentile(expectancies, [2.5, 97.5])
print(f"Expectancy: ${expectancy:.2f}")
print(f"95% CI: [${ci_lower:.2f}, ${ci_upper:.2f}]")
print(f"Alpha Proven: {'YES' if ci_lower > 0 else 'NO'}")
```

---

## PHASE 3: ROBUSTNESS TESTING (Week 2-3)
### What: Prove edge is robust, not curve-fit

#### 3.1 Walk-Forward Validation
```
Purpose: Test on data the strategy never saw during development

Method:
  1. Split data into 10 periods (70-30 train-test split)
  2. Train on period 1 (70% of data)
  3. Test on period 1 (30% of data)
  4. Train on periods 1-2 (70%)
  5. Test on period 2 (30%)
  ... repeat ...

Result: If all test periods are profitable, edge is real
```

**Implementation:**
```python
def walk_forward_test(prices, n_splits=10):
    """Walk-forward validation with expanding window"""
    results = []
    n = len(prices)
    train_size = int(n * 0.7)
    test_size = int(n * 0.3)

    for i in range(0, n - train_size - test_size, test_size // 2):
        train_data = prices[i:i+train_size]
        test_data = prices[i+train_size:i+train_size+test_size]

        # Train algorithm on training data
        algo_train = HurstCyclicAlgorithm(train_data)
        algo_train.run()

        # Test on out-of-sample data
        algo_test = HurstCyclicAlgorithm(test_data)
        algo_test.components = algo_train.components  # Use trained cycles
        report_test = algo_test.run()

        results.append({
            'period': i,
            'train_start': i,
            'train_end': i+train_size,
            'test_start': i+train_size,
            'test_end': i+train_size+test_size,
            'win_rate': report_test['win_rate'],
            'return': report_test['total_return_pct'],
            'sharpe': report_test['sharpe_ratio'],
        })

    # Check consistency
    win_rates = [r['win_rate'] for r in results]
    returns = [r['return'] for r in results]
    sharpes = [r['sharpe'] for r in results]

    print("Walk-Forward Results:")
    print(f"Avg Win Rate: {np.mean(win_rates):.1%} (Std: {np.std(win_rates):.1%})")
    print(f"Avg Return: {np.mean(returns):.2f}% (Std: {np.std(returns):.2f}%)")
    print(f"Avg Sharpe: {np.mean(sharpes):.2f} (Std: {np.std(sharpes):.2f})")
    print(f"Profitable periods: {sum(1 for r in returns if r > 0)}/{len(results)}")

    return results
```

#### 3.2 Parameter Sensitivity Analysis
```
Purpose: Prove the strategy isn't over-optimized

Method: Test performance across parameter ranges:
  - Confluence threshold: 20%, 25%, 30%, 35%, 40%, 45%, 50%
  - Risk per trade: 1%, 1.5%, 2%, 2.5%, 3%
  - Stop loss distance: 1%, 2%, 3%, 4%, 5%

Result: If all parameter values are profitable, not curve-fit
```

**Implementation:**
```python
def sensitivity_analysis():
    """Test performance across parameter ranges"""

    confluence_thresholds = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    risk_per_trades = [0.01, 0.015, 0.02, 0.025, 0.03]

    results = []

    for confluence_threshold in confluence_thresholds:
        for risk_per_trade in risk_per_trades:
            # Create algorithm with these parameters
            algo = HurstCyclicAlgorithm(data)
            algo.psych_barriers.min_confluence_threshold = confluence_threshold
            algo.backtest.risk_per_trade = risk_per_trade

            report = algo.run()

            results.append({
                'confluence': confluence_threshold,
                'risk': risk_per_trade,
                'win_rate': report['win_rate'],
                'return': report['total_return_pct'],
                'sharpe': report['sharpe_ratio'],
            })

    # Create heatmap
    results_df = pd.DataFrame(results)
    heatmap = results_df.pivot_table(
        values='return',
        index='confluence',
        columns='risk',
        aggfunc='mean'
    )

    print("Parameter Sensitivity Heatmap (Returns):")
    print(heatmap)

    # Check: Are most parameters positive?
    positive_params = sum(1 for r in results if r['return'] > 0)
    total_params = len(results)
    print(f"\nPositive combinations: {positive_params}/{total_params} ({positive_params/total_params:.1%})")
    print(f"Alpha Proof: {'STRONG' if positive_params > total_params * 0.7 else 'WEAK'}")

    return results_df
```

#### 3.3 Equity Curve Stability
```
Purpose: Prove returns are consistent, not due to one lucky trade

Metrics:
  - Number of consecutive losing trades (max)
  - Consecutive winning trades (max)
  - Largest single win (% of total return)
  - Largest single loss (% of max drawdown)

Result: If no single trade drives returns, edge is robust
```

**Implementation:**
```python
def analyze_equity_curve_stability(trades):
    """Analyze equity curve for stability and robustness"""

    # Consecutive winners/losers
    max_consec_wins = 0
    max_consec_losses = 0
    current_wins = 0
    current_losses = 0

    for trade in trades:
        if trade.pnl > 0:
            current_wins += 1
            current_losses = 0
            max_consec_wins = max(max_consec_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_consec_losses = max(max_consec_losses, current_losses)

    # Single trade impact
    total_pnl = sum(t.pnl for t in trades)
    largest_win = max((t.pnl for t in trades if t.pnl > 0), default=0)
    largest_loss = min((t.pnl for t in trades if t.pnl < 0), default=0)

    win_impact = largest_win / total_pnl if total_pnl > 0 else 0
    loss_impact = abs(largest_loss) / total_pnl if total_pnl > 0 else 0

    print("Equity Curve Stability:")
    print(f"Max Consecutive Wins: {max_consec_wins}")
    print(f"Max Consecutive Losses: {max_consec_losses}")
    print(f"Largest Win as % of Total Profit: {win_impact:.1%}")
    print(f"Largest Loss as % of Total Profit: {loss_impact:.1%}")

    if win_impact > 0.5:
        print(f"WARNING: Single trade ({win_impact:.1%}) drives most returns - may not be robust")
    else:
        print(f"OK: Returns distributed across multiple trades")

    return {
        'max_consec_wins': max_consec_wins,
        'max_consec_losses': max_consec_losses,
        'largest_win_pct': win_impact,
        'largest_loss_pct': loss_impact,
    }
```

---

## PHASE 4: RISK ANALYSIS (Week 3-4)
### What: Understand and quantify the risks

#### 4.1 Drawdown Analysis
```
Maximum Drawdown: Largest peak-to-trough loss
Recovery Time: Days to recover from max drawdown
Drawdown Duration: How long underwater

Expected for good strategy:
  - Max DD: < 20% (for 10-20% annual return)
  - Recovery: < 6 months
  - Frequency: Drawdown once per 2-3 years
```

**Implementation:**
```python
def analyze_drawdowns(equity_curve):
    """Analyze all drawdowns in strategy"""

    # Calculate running maximum
    running_max = equity_curve.expanding().max()

    # Calculate drawdown from running maximum
    drawdown = (equity_curve - running_max) / running_max

    # Find all drawdown periods
    drawdowns = []
    in_dd = False
    dd_start = None

    for i in range(len(drawdown)):
        if drawdown.iloc[i] < -0.01 and not in_dd:  # Start of drawdown
            in_dd = True
            dd_start = i
        elif drawdown.iloc[i] >= 0 and in_dd:  # End of drawdown
            in_dd = False
            dd_end = i
            dd_magnitude = drawdown.iloc[dd_start:dd_end].min()
            dd_duration = dd_end - dd_start

            drawdowns.append({
                'start': dd_start,
                'end': dd_end,
                'magnitude': dd_magnitude,
                'duration_days': dd_duration,
                'recovery_date': equity_curve.index[dd_end],
            })

    print(f"Total Drawdowns: {len(drawdowns)}")
    print(f"Max Drawdown: {min(d['magnitude'] for d in drawdowns):.2%}")
    print(f"Avg Drawdown Duration: {np.mean([d['duration_days'] for d in drawdowns]):.0f} days")
    print(f"Longest Drawdown: {max(d['duration_days'] for d in drawdowns):.0f} days")

    return drawdowns
```

#### 4.2 Value at Risk (VaR) & Expected Shortfall
```
VaR: Worst 5% of outcomes
CVaR: Average of worst 5% of outcomes

Expected:
  - VaR (5%): -5% per month or worse (once per 20 months)
  - CVaR (5%): -8% per month or worse
```

**Implementation:**
```python
def calculate_var_cvar(returns, confidence=0.95):
    """Calculate VaR and CVaR"""

    returns_sorted = np.sort(returns)
    n = len(returns)

    # VaR at 95% confidence (5% worst outcomes)
    var_index = int((1 - confidence) * n)
    var = returns_sorted[var_index]

    # CVaR (average of worst 5%)
    cvar = returns_sorted[:var_index].mean()

    print(f"Value at Risk (95% confidence): {var:.2%}")
    print(f"Expected Shortfall (CVaR): {cvar:.2%}")

    return var, cvar
```

#### 4.3 Sortino Ratio
```
Like Sharpe but only penalizes downside volatility
Expected: > 1.5 for good strategy

Formula: (Return - Risk-Free) / Downside Deviation
```

**Implementation:**
```python
def calculate_sortino_ratio(returns, risk_free_rate=0.045):
    """Calculate Sortino ratio"""

    excess_return = returns.mean() * 252 - risk_free_rate

    # Downside deviation (only negative returns)
    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std() * np.sqrt(252)

    sortino = excess_return / downside_std if downside_std > 0 else 0

    print(f"Sortino Ratio: {sortino:.2f}")
    print(f"Target: > 1.5")

    return sortino
```

---

## PHASE 5: MARKET REGIME ANALYSIS (Week 4)
### What: Does the strategy work in ALL market conditions?

#### 5.1 Trending vs Mean-Reverting Markets
```
Separate backtest results by market regime:
  - Uptrend: SMA(20) > SMA(200)
  - Downtrend: SMA(20) < SMA(200)
  - Sideways: Volatility < 20th percentile

Expected:
  - Strategy should work in mean-reverting (sideways) periods
  - May underperform in strong trends
```

**Implementation:**
```python
def regime_analysis(prices):
    """Analyze strategy performance by market regime"""

    # Calculate regimes
    ma20 = talib.SMA(prices, 20)
    ma200 = talib.SMA(prices, 200)

    regimes = []
    for i in range(200, len(prices)):
        if ma20[i] > ma200[i]:
            regime = 'UPTREND'
        elif ma20[i] < ma200[i]:
            regime = 'DOWNTREND'
        else:
            regime = 'SIDEWAYS'
        regimes.append(regime)

    # Split trades by regime
    uptrend_trades = [t for t, r in zip(trades, regimes) if r == 'UPTREND']
    downtrend_trades = [t for t, r in zip(trades, regimes) if r == 'DOWNTREND']
    sideways_trades = [t for t, r in zip(trades, regimes) if r == 'SIDEWAYS']

    print("Performance by Market Regime:")
    print(f"Uptrend: {len(uptrend_trades)} trades, "
          f"{sum(1 for t in uptrend_trades if t.pnl > 0)/len(uptrend_trades):.1%} WR")
    print(f"Downtrend: {len(downtrend_trades)} trades, "
          f"{sum(1 for t in downtrend_trades if t.pnl > 0)/len(downtrend_trades):.1%} WR")
    print(f"Sideways: {len(sideways_trades)} trades, "
          f"{sum(1 for t in sideways_trades if t.pnl > 0)/len(sideways_trades):.1%} WR")

    return regimes
```

#### 5.2 Volatility Regime Testing
```
Split by VIX level:
  - Low Vol: VIX < 15
  - Medium Vol: VIX 15-25
  - High Vol: VIX > 25

Strategy should work best in medium/high vol
```

#### 5.3 Multi-Asset Consistency
```
Test across different asset classes:
  - Equities (SPY, QQQ, IWM)
  - Bonds (TLT)
  - Commodities (GLD, DBC)
  - Forex (EUR/USD)

If >70% of assets are profitable, alpha is real
```

---

## PHASE 6: EDGE DECOMPOSITION (Week 5)
### What: Which part creates the alpha?

#### 6.1 Ablation Testing
```
Remove each component and test individually:
  1. Full system (all features)
  2. Remove FLD signals (use only edge/mid-band)
  3. Remove spectral signatures (equal weight all cycles)
  4. Remove phase analysis (no phase filtering)
  5. Remove psychological barriers
  6. Random entry signal (baseline)

Result: Identify which features create alpha
```

**Implementation:**
```python
def ablation_test():
    """Test each feature independently"""

    results = {}

    # Test 1: Full system
    algo_full = HurstCyclicAlgorithm(data, use_fld=True)
    results['Full System'] = algo_full.run()

    # Test 2: Without FLD
    algo_no_fld = HurstCyclicAlgorithm(data, use_fld=False)
    results['No FLD'] = algo_no_fld.run()

    # Test 3: Without spectral signatures (equal weight)
    # (requires code modification)

    # Test 4: Without phase analysis
    # (requires code modification)

    # Test 5: Without psychological barriers
    # (disable confluence filtering)

    # Test 6: Random signals (baseline)
    random_signals = generate_random_signals(data)
    results['Random Baseline'] = backtest(random_signals)

    print("Ablation Test Results:")
    for name, report in results.items():
        print(f"{name:20s}: {report['win_rate']:.1%} WR, "
              f"{report['total_return_pct']:.2f}% return, "
              f"{report['sharpe_ratio']:.2f} Sharpe")

    return results
```

#### 6.2 Cycle Profitability Analysis
```
Which cycles are most profitable?
  - 18-month cycle: X% of wins
  - 40-week cycle: X% of wins
  - 20-week cycle: X% of wins
  - etc.

Result: Know which cycles to focus on
```

**Implementation:**
```python
def analyze_cycle_profitability(trades, components):
    """Which cycles produce the most profit?"""

    cycle_profits = {}

    for comp in components:
        cycle_trades = [t for t in trades if comp.label in t.cycles_aligned]
        if cycle_trades:
            total_pnl = sum(t.pnl for t in cycle_trades)
            win_rate = sum(1 for t in cycle_trades if t.pnl > 0) / len(cycle_trades)

            cycle_profits[comp.label] = {
                'trades': len(cycle_trades),
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'avg_trade': total_pnl / len(cycle_trades),
            }

    print("Profitability by Cycle:")
    for cycle, stats in sorted(cycle_profits.items()):
        print(f"{cycle:12s}: {stats['trades']:3d} trades, "
              f"{stats['win_rate']:.1%} WR, "
              f"${stats['avg_trade']:.2f} avg")
```

---

## PHASE 7: REAL-WORLD CONSTRAINTS (Week 5-6)
### What: Will it work in real trading?

#### 7.1 Liquidity Analysis
```
Can we actually execute these trades?
  - Average position size: $X
  - Average daily volume: $Y
  - Slippage for our size: Z%
  - Realistic execution cost: Z%

Test that costs < potential profit
```

**Implementation:**
```python
def liquidity_analysis(trades, symbol):
    """Analyze if trades can be executed in real market"""

    # Get historical volume data
    volume_data = yf.download(symbol)['Volume']

    for trade in trades:
        position_notional = trade.size * trade.entry_price
        date_volume = volume_data.iloc[trade.entry_bar]
        position_pct = position_notional / (date_volume * trade.entry_price)

        # Estimate slippage (market microstructure)
        # Rule of thumb: sqrt(position%) × 0.1% slippage
        estimated_slippage = np.sqrt(position_pct) * 0.001

        print(f"Trade {trade.entry_bar}: "
              f"Size ${position_notional:.0f}, "
              f"Vol ${date_volume * trade.entry_price:.0f}, "
              f"Pct {position_pct:.1%}, "
              f"Est Slippage {estimated_slippage:.3%}")
```

#### 7.2 Overnight Gap Analysis
```
How often do gaps hurt us?
  - Gaps against position: X% of trades
  - Average gap size: Y%
  - Max gap loss: Z%

Decide: Hold overnight or close at market close?
```

#### 7.3 Market Hours Constraint
```
Are our signals generated during market hours?
  Can we actually execute them?
```

---

## PHASE 8: FORWARD TESTING (Week 6-7)
### What: Does it work on data we haven't seen yet?

#### 8.1 Out-of-Sample Testing
```
Save 20% of data for testing:
  - Train algorithm on 80% (2019-2024)
  - Test on 20% (2025-2026)
  - Verify performance matches backtest
```

#### 8.2 Paper Trading
```
Simulate trading without real money:
  - Generate signals on real-time data
  - Execute simulated trades
  - Run for 3-6 months
  - Compare simulated vs strategy returns
```

#### 8.3 Time-Series Cross-Validation
```
Multiple train-test splits:
  - Block cross-validation (don't mix train/test data chronologically)
  - Time series split (respect temporal order)
  - Rolling window validation

Expected: Consistent performance across all splits
```

**Implementation:**
```python
def time_series_cross_validation(data, n_splits=5):
    """Time series aware cross-validation"""

    n = len(data)
    split_size = n // (n_splits + 1)

    results = []

    for i in range(1, n_splits + 1):
        train_start = 0
        train_end = i * split_size
        test_start = train_end
        test_end = min((i + 1) * split_size, n)

        train_data = data.iloc[train_start:train_end]
        test_data = data.iloc[test_start:test_end]

        algo_train = HurstCyclicAlgorithm(train_data)
        algo_train.run()

        algo_test = HurstCyclicAlgorithm(test_data)
        algo_test.components = algo_train.components
        report_test = algo_test.run()

        results.append({
            'fold': i,
            'train_period': f"{train_data.index[0]} - {train_data.index[-1]}",
            'test_period': f"{test_data.index[0]} - {test_data.index[-1]}",
            'win_rate': report_test['win_rate'],
            'return': report_test['total_return_pct'],
            'sharpe': report_test['sharpe_ratio'],
        })

    results_df = pd.DataFrame(results)
    print(results_df)

    return results_df
```

---

## PHASE 9: STATISTICAL TESTS (Week 7)
### What: Formal proof of alpha

#### 9.1 Autocorrelation Test
```
Are returns random or patterned?
  - ACF (autocorrelation function): Should be near 0
  - Ljung-Box test: p > 0.05 (returns are random)

If ACF is high and LB p < 0.05, might be exploitable pattern
```

**Implementation:**
```python
from statsmodels.stats.diagnostic import acorr_ljungbox

def autocorrelation_test(returns):
    """Test if returns are random or autocorrelated"""

    # ACF plot
    from statsmodels.graphics.tsaplots import plot_acf
    plot_acf(returns, lags=20)

    # Ljung-Box test
    lb_result = acorr_ljungbox(returns, lags=[10, 20], return_df=True)

    print("Ljung-Box Test Results:")
    print(lb_result)

    if (lb_result['lb_pvalue'] < 0.05).any():
        print("WARNING: Autocorrelation detected (may be exploitable)")
    else:
        print("OK: Returns appear random (no exploitable patterns)")
```

#### 9.2 Distribution Normality Test
```
Are returns normally distributed?
  - Jarque-Bera test: p > 0.05 (normal)
  - Shapiro-Wilk test: p > 0.05 (normal)

If not normal, VaR calculations may be wrong
```

#### 9.3 Stationarity Test
```
Is equity curve stationary (mean doesn't drift)?
  - Augmented Dickey-Fuller test: p < 0.05 (stationary)

If non-stationary, may not predict future
```

---

## PHASE 10: COMPARISON TO KNOWN STRATEGIES (Week 7-8)
### What: How good is this vs other strategies?

#### 10.1 vs Buy-and-Hold
```
Equal investment in SPY vs Hurst strategy
Result: Should beat by >3% annually after costs
```

#### 10.2 vs Momentum (Trend Following)
```
Buy when 50MA > 200MA, hold for 50 bars
Result: Hurst should work better in mean-revert periods
```

#### 10.3 vs Mean Reversion
```
Buy when price < -2 std from MA, sell when > -0.5 std
Result: Direct competitor; should match or beat
```

#### 10.4 vs Ensemble of Strategies
```
Can combine Hurst with other strategies?
- 50% Hurst + 50% Momentum: Better risk-adjusted returns?
- Diversification benefit: YES = alpha is real
```

---

## PHASE 11: FINAL REPORT (Week 8)
### What: Professional-grade summary

**Standard Quant Report Format:**

```
EXECUTIVE SUMMARY
  - Annualized Return: X%
  - Sharpe Ratio: X.XX
  - Max Drawdown: -X%
  - Win Rate: X%
  - Alpha (vs SPY): +X% annually

STATISTICAL SIGNIFICANCE
  - Win rate p-value: X (significant: YES/NO)
  - Return t-test p-value: X (significant: YES/NO)
  - Sharpe 95% CI: [X.XX, Y.YY]

ROBUSTNESS
  - Walk-forward avg return: X%
  - Parameter sensitivity: Profitable in X% of configurations
  - Equity curve stability: Single trade impact: X%

RISK METRICS
  - Value at Risk (5%): -X%
  - Expected Shortfall: -X%
  - Sortino Ratio: X.XX
  - Calmar Ratio (return/DD): X.XX

MARKET REGIMES
  - Trending markets: X% WR
  - Mean-reverting markets: X% WR (BEST)
  - Low volatility: X% WR
  - High volatility: X% WR

EDGE DECOMPOSITION
  - Full system: X% WR
  - Without FLD: X% WR (contribution: X%)
  - Without phase: X% WR (contribution: X%)
  - Without spectral: X% WR (contribution: X%)
  - Random baseline: X% WR

FORWARD TESTING
  - Out-of-sample return: X%
  - Paper trading return: X%
  - Time-series CV avg: X%

RECOMMENDATION
  ✓ APPROVED FOR LIVE TRADING if:
    - All statistical tests show p < 0.05
    - Walk-forward return > 10% annually
    - Sharpe > 1.5
    - Max DD < 20%
```

---

## EXECUTION TIMELINE

### Week 1: Baseline + Significance Testing
- Build buy-and-hold benchmark
- Run binomial test on win rate
- Calculate alpha vs market

### Week 2: Robustness Testing
- Walk-forward validation
- Parameter sensitivity
- Equity curve stability

### Week 3-4: Risk Analysis
- Drawdown analysis
- VaR / CVaR calculation
- Sortino / Calmar ratios

### Week 4: Market Regimes
- Trending vs mean-reverting
- Volatility analysis
- Multi-asset testing

### Week 5: Edge Decomposition
- Ablation testing
- Cycle profitability
- Feature importance

### Week 5-6: Real-World Constraints
- Liquidity analysis
- Gap analysis
- Execution feasibility

### Week 6-7: Forward Testing
- Out-of-sample testing
- Paper trading simulation
- Time-series CV

### Week 7: Statistical Tests
- Autocorrelation analysis
- Normality testing
- Stationarity check

### Week 7-8: Comparison & Report
- vs Momentum, Mean Reversion, Buy-and-Hold
- Ensemble analysis
- Final report

---

## SUCCESS CRITERIA

✅ **Alpha is PROVEN if:**
1. Win rate p-value < 0.05 (statistically significant)
2. Walk-forward average return > 8% annually
3. Sharpe ratio > 1.5 (risk-adjusted)
4. Max drawdown < 20%
5. Out-of-sample performance similar to in-sample
6. >70% of parameter combinations profitable
7. Profit from multiple cycles (not one lucky trade)
8. Works on multiple assets
9. Positive alpha vs S&P 500
10. Positive results in different market regimes

---

## CRITICAL SUCCESS FACTORS

🔴 **Deal-breakers (if ANY occur, strategy fails):**
- Single trade accounts for >50% of profits
- p-value > 0.05 for statistical tests
- Max DD > 30%
- Out-of-sample return < 5%
- Only works in ONE market regime
- Sharpe < 1.0

---

**This is the professional-grade validation that institutional quants use. Follow this plan and you'll know definitively if the strategy has real alpha.**

