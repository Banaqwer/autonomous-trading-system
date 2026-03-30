# Hurst Cyclic Trading - Usage Examples

---

## Example 1: Quick Test with Synthetic Data

```python
from hurst_cyclic_trading import HurstCyclicAlgorithm, load_sample_data

# Load synthetic data (built-in test data)
df = load_sample_data()

# Run algorithm
algo = HurstCyclicAlgorithm(
    df,
    price_col="Close",
    risk_per_trade=0.02,
    initial_capital=100000
)
results = algo.run()

# Results are printed to console
# Access algo.trades to get detailed trade list
# Access algo.equity_df for equity curve
```

---

## Example 2: Load Real Data from CSV

```python
from hurst_production import DataManager, HurstCyclicAlgorithm

# Load from CSV file
data = DataManager.load_from_csv("btc_daily.csv")

# Verify data
print("Data shape:", data.shape)
print("Date range:", data.index[0], "to", data.index[-1])
print("Price range:", data['Close'].min(), "to", data['Close'].max())

# Run algorithm
algo = HurstCyclicAlgorithm(data, risk_per_trade=0.02)
results = algo.run()

# Save results
algo.trades_df = pd.DataFrame([
    {
        'entry_bar': t.entry_bar,
        'exit_bar': t.exit_bar,
        'side': t.side.value,
        'entry_price': t.entry_price,
        'exit_price': t.exit_price,
        'pnl': t.pnl,
        'r_multiple': t.r_multiple,
    }
    for t in algo.trades
])
algo.trades_df.to_csv('trades.csv', index=False)
```

---

## Example 3: Load from Yahoo Finance

```python
from hurst_production import DataManager, HurstCyclicAlgorithm

# Download data
data = DataManager.load_from_yfinance(
    symbol="BTC-USD",
    start="2022-01-01",
    end="2024-12-31"
)

# Run
algo = HurstCyclicAlgorithm(data)
results = algo.run()

# Plot equity curve
import matplotlib.pyplot as plt
algo.equity_df['equity'].plot()
plt.title("Equity Curve")
plt.show()
```

---

## Example 4: Walk-Forward Testing

```python
from hurst_production import HurstConfig, DataManager, WalkForwardTester

# Load data
data = DataManager.create_synthetic_btc_like(n=1000)

# Configure walk-forward test
tester = WalkForwardTester(
    data,
    train_window=500,     # train on 500 bars
    test_window=100,      # test on 100 bars
    step_size=50          # advance 50 bars each iteration
)

# Run
results = tester.run()

# Results contain:
# - results['total_windows']: number of test windows
# - results['aggregate']: out-of-sample performance metrics
# - results['windows']: per-window results

print(f"Total windows: {results['total_windows']}")
if results.get('aggregate'):
    agg = results['aggregate']
    print(f"OOS Sharpe: {agg['sharpe_ratio']:.2f}")
    print(f"OOS Win Rate: {agg['win_rate']:.1%}")
    print(f"OOS Trades: {agg['total_trades']}")
```

---

## Example 5: Ablation Analysis

```python
from hurst_production import AblationTester, DataManager

# Load data
data = DataManager.create_synthetic_btc_like(n=800)

# Run ablation tests
tester = AblationTester(data)
results = tester.run()

# Results show impact of disabling each cycle
# Sorted by importance score

print("\nMost important cycles:")
for i, ablation in enumerate(results['ablations'][:3]):
    print(f"{i+1}. {ablation['disabled_cycle']}")
    print(f"   Sharpe impact: {ablation['impact_sharpe']:+.2f}")
    print(f"   Win rate impact: {ablation['impact_win_rate']:+.1%}")
    print(f"   Importance score: {ablation['importance_score']:.2f}")
```

---

## Example 6: Production System with All Features

```python
from hurst_production import HurstConfig, DataManager, ProductionExecutor

# Configure
config = HurstConfig(
    symbol="BTC/USD",
    timeframe="daily",
    risk_per_trade=0.02,
    use_parabolic_interpolation=True,
    walk_forward_enabled=True,
    walk_forward_train_window=400,
    walk_forward_test_window=100,
    walk_forward_step=50,
    ablation_enabled=True,
)

# Load data
data = DataManager.load_from_csv("btc_daily.csv")

# Execute full pipeline
executor = ProductionExecutor(config, data)
results = executor.run()

# Access results
if 'walk_forward' in results:
    wf_results = results['walk_forward']
    print(f"Walk-forward Sharpe: {wf_results['aggregate']['sharpe_ratio']:.2f}")

if 'ablation' in results:
    abl_results = results['ablation']
    print(f"Most important cycle: {abl_results['ablations'][0]['disabled_cycle']}")

if 'main_run' in results:
    main_results = results['main_run']
    print(f"Total return: {main_results['total_return_pct']:.1f}%")
```

---

## Example 7: Custom Configuration

```python
from hurst_production import HurstConfig, DataManager, HurstCyclicAlgorithm

# Conservative strategy (fewer trades, higher confidence)
config = HurstConfig(
    risk_per_trade=0.01,              # only 1% per trade
    min_confluence_score=0.50,        # only strongest signals
    edge_band_enabled=True,
    mid_band_enabled=False,           # only mid-band (no edge-band)
)

data = DataManager.create_synthetic_btc_like(n=600)
algo = HurstCyclicAlgorithm(data, risk_per_trade=config.risk_per_trade)
results = algo.run()

print(f"Conservative strategy: {results['total_trades']} trades")
print(f"Average win: ${results['avg_winner']:.2f}")
print(f"Sharpe: {results['sharpe_ratio']:.2f}")

# Aggressive strategy (more trades, lower confluence threshold)
config2 = HurstConfig(
    risk_per_trade=0.05,              # 5% per trade
    min_confluence_score=0.20,        # lower threshold
    edge_band_enabled=True,
    mid_band_enabled=True,            # both signal types
)

algo2 = HurstCyclicAlgorithm(data, risk_per_trade=0.05)
results2 = algo2.run()

print(f"\nAggressive strategy: {results2['total_trades']} trades")
print(f"Sharpe: {results2['sharpe_ratio']:.2f}")
```

---

## Example 8: Inspecting Signals

```python
from hurst_cyclic_trading import HurstCyclicAlgorithm
from hurst_production import DataManager

data = DataManager.create_synthetic_btc_like(n=500)

# Run algorithm and collect signals
algo = HurstCyclicAlgorithm(data)

# Before running full algo, get detector and signal engine
from hurst_cyclic_trading import CycleDetector, HurstSignalEngine

prices = data['Close'].values
detector = CycleDetector(prices)
components = detector.detect_cycles()

print(f"\nDetected {len(components)} cycles:")
for c in components:
    print(f"  {c.label}: period={c.period:.0f} bars, "
          f"amplitude={c.amplitude:.4f}, confidence={c.confidence:.2f}")

# Generate signals
engine = HurstSignalEngine(prices, components)
signals = engine.generate_signals()

print(f"\nGenerated {len(signals)} signals:")
for sig in signals[:5]:  # show first 5
    print(f"  Bar {sig.bar}: {sig.side.value} @ ${sig.price:.2f}, "
          f"confluence={sig.confluence_score:.2f}")
```

---

## Example 9: Analyzing Envelope Accuracy

```python
from hurst_production import ParabolicInterpolator
from hurst_cyclic_trading import EnvelopeEngine, CycleDetector
from hurst_production import DataManager

data = DataManager.create_synthetic_btc_like(n=600)
prices = data['Close'].values

# Detect cycles
detector = CycleDetector(prices)
components = detector.detect_cycles()

# Get the primary trading cycle
tc = components[1] if len(components) > 1 else components[0]
period = int(tc.period)

print(f"Analyzing envelopes for {tc.label} cycle (period={period})")

# Standard linear envelope
upper_linear, lower_linear, _ = EnvelopeEngine.build_curvilinear_envelopes(prices, period)

# Parabolic envelope
upper_parabolic, lower_parabolic, _ = ParabolicInterpolator.build_parabolic_envelopes(prices, period)

# Compare
import numpy as np
diff_upper = np.abs(upper_parabolic - upper_linear)
diff_lower = np.abs(lower_parabolic - lower_linear)
valid_up = diff_upper[~np.isnan(diff_upper)]
valid_low = diff_lower[~np.isnan(diff_lower)]

print(f"Upper envelope differences:")
print(f"  Mean: {valid_up.mean():.4f}")
print(f"  Max: {valid_up.max():.4f}")
print(f"Lower envelope differences:")
print(f"  Mean: {valid_low.mean():.4f}")
print(f"  Max: {valid_low.max():.4f}")
print(f"\nParabolic interpolation produced smoother curves")
```

---

## Example 10: Real-Time Signal Monitoring

```python
from hurst_cyclic_trading import HurstCyclicAlgorithm, CycleDetector, HurstSignalEngine
from hurst_production import DataManager
import pandas as pd

# Initial data
data = DataManager.load_from_csv("btc_daily.csv")

# Process initial signal
def get_latest_signal(data):
    prices = data['Close'].values
    detector = CycleDetector(prices)
    components = detector.detect_cycles()

    if not components:
        return None

    engine = HurstSignalEngine(prices, components)
    signals = engine.generate_signals()

    if signals:
        return signals[-1]  # latest signal
    return None

# Check signal now
sig = get_latest_signal(data)
if sig:
    print(f"Latest signal (bar {sig.bar}):")
    print(f"  Direction: {sig.side.value}")
    print(f"  Timing: {sig.timing_type}")
    print(f"  Confluence: {sig.confidence_score:.1%}")
    print(f"  Entry: ${sig.price:.2f}, Stop: ${sig.stop_price:.2f}")

# When new data arrives (e.g., daily close)
# append to data and re-check:
# new_bar = pd.DataFrame(...)
# data = pd.concat([data, new_bar])
# sig = get_latest_signal(data)
```

---

## Example 11: Multiple Timeframe Analysis

```python
from hurst_production import DataManager, HurstCyclicAlgorithm

# Load daily data
daily_data = DataManager.load_from_csv("btc_daily.csv")

# Resample to weekly (for higher timeframe analysis)
weekly_data = daily_data.resample('W').agg({
    'Open': 'first',
    'High': 'max',
    'Low': 'min',
    'Close': 'last',
    'Volume': 'sum',
})

# Run on daily
algo_daily = HurstCyclicAlgorithm(daily_data, risk_per_trade=0.01)
print("=== DAILY ===")
results_daily = algo_daily.run()

# Run on weekly
algo_weekly = HurstCyclicAlgorithm(weekly_data, risk_per_trade=0.02)
print("\n=== WEEKLY ===")
results_weekly = algo_weekly.run()

# Compare
print(f"\nDaily: {results_daily['total_trades']} trades, "
      f"Sharpe={results_daily['sharpe_ratio']:.2f}")
print(f"Weekly: {results_weekly['total_trades']} trades, "
      f"Sharpe={results_weekly['sharpe_ratio']:.2f}")
```

---

## Example 12: Saving and Loading Configuration

```python
from hurst_production import HurstConfig

# Create and customize config
config = HurstConfig(
    symbol="BTC/USD",
    risk_per_trade=0.02,
    min_confluence_score=0.40,
    walk_forward_enabled=True,
)

# Save to file
config.save("my_trading_config.json")
print("Config saved to my_trading_config.json")

# Later, load from file
config_loaded = HurstConfig.load("my_trading_config.json")
print(f"Loaded config: {config_loaded.symbol}, risk={config_loaded.risk_per_trade}")

# Use in executor
from hurst_production import ProductionExecutor, DataManager
data = DataManager.load_from_csv("data.csv")
executor = ProductionExecutor(config_loaded, data)
results = executor.run()
```

---

## Tips & Best Practices

1. **Start with walk-forward testing** — Always validate OOS before trusting backtest results
2. **Use ablation to understand** — See which cycles actually matter in your data
3. **Adjust confluence threshold** — Higher = fewer, stronger signals; Lower = more signals
4. **Monitor by timeframe** — Daily, weekly, monthly may have different cycle strengths
5. **Re-detect cycles periodically** — Markets change; re-run detector monthly
6. **Use position sizing** — Risk per trade should match your portfolio size and risk tolerance
7. **Track real vs predicted** — Monitor gap between backtest and live performance
8. **Diversify across assets** — Test on multiple markets (crypto, forex, commodities)

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'hurst_cyclic_trading'"**
- Ensure both files are in the same directory: `hurst_cyclic_trading.py` and `hurst_production.py`

**"No signals generated"**
- Check: min_confluence_score threshold
- Check: data has enough bars (300+ recommended)
- Check: cycles were detected in output

**"MemoryError on large dataset"**
- Process in chunks (walk-forward does this automatically)
- Reduce data size (don't use every tick, use daily or higher)

**"Sharpe ratio is NaN"**
- Not enough trades or zero standard deviation returns
- Increase timeframe or adjust signal thresholds

---

## Further Reading

- See `README_HURST_PRODUCTION.md` for complete documentation
- See `hurst_cyclic_trading.py` docstrings for implementation details
- Reference: Hurst, J.M. "The Profit Magic of Stock Transaction Timing" (1970)
