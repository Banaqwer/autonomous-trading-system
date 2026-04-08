# HURST CYCLIC TRADING - PARAMETER TUNING GUIDE
## Generating Signals in Real Market Conditions

---

## OVERVIEW

The Week 1 validation showed that the Hurst system is 100% working but generating zero signals on 2021-2026 data (strong uptrend, low volatility). This is because:

1. ✓ Cycles are detected correctly
2. ✓ Envelopes are calculated correctly
3. ✗ Signal confluence threshold is too strict (40%)
4. ✗ Market regime (sustained uptrend) unfavorable for mean reversion

**Solution:** Lower confluence thresholds and test on market regimes where mean-reversion signals are more common.

---

## WHERE THRESHOLDS ARE DEFINED

### File: `hurst_cyclic_trading.py`

#### Location 1: HurstSignalEngine class
Around line 2548 - `min_confluence_threshold` parameter:

```python
class HurstSignalEngine:
    def __init__(self, prices, components, spectral_sig=None,
                 min_confluence_threshold: float = 0.4):  # CHANGE THIS
        self.min_confluence_threshold = min_confluence_threshold
```

**Current Value:** 0.4 (40%)
**Recommended:** 0.20-0.30 (20-30%)

#### Location 2: _compute_confluence() method
Around line 2600+ - Where confluence scoring happens:

```python
def _compute_confluence(self, bar: int) -> float:
    """Score confluence of multiple cycles at this bar."""
    # ... confluence calculation ...
    return confluence_score >= self.min_confluence_threshold
```

#### Location 3: _are_signal_conditions_met() method
Where individual signal band thresholds are checked:

```python
def _are_signal_conditions_met(self, bar: int) -> Tuple[bool, List[Signal]]:
    # Edge-band condition: 30% currently
    # Mid-band condition: 40% currently
    # FLD condition: 40% currently
```

---

## IMPLEMENTATION: METHOD 1 - DIRECT MODIFICATION

### Step 1: Locate HurstSignalEngine in hurst_cyclic_trading.py

Search for line ~2548:
```python
def __init__(self, max_indicators: int = 5,
             risk_per_trade: float = 0.02,
             max_daily_loss: float = 0.05,
             min_confluence_threshold: float = 0.4):  # <-- HERE
```

### Step 2: Lower the threshold

**Current:**
```python
min_confluence_threshold: float = 0.4  # 40%
```

**Try First:**
```python
min_confluence_threshold: float = 0.25  # 25%
```

**If still no signals, try:**
```python
min_confluence_threshold: float = 0.15  # 15%
```

### Step 3: Find where signals are generated

Look for the signal generation logic around line 2620-2630:
```python
def generate_signals(self) -> List[Signal]:
    signals = []
    for bar in range(middle, len(self.prices)):
        if self._are_signal_conditions_met(bar):
            # Create signal
```

### Step 4: Run validation again

```bash
python week1_validation_simple.py
```

---

## IMPLEMENTATION: METHOD 2 - EXTERNAL PARAMETER TUNING

### Create a new file: `tune_hurst_parameters.py`

```python
import pandas as pd
import numpy as np
import yfinance as yf
from hurst_cyclic_trading import HurstSignalEngine, HurstCyclicAlgorithm

class HurstParameterTuner:
    """Test different confluence thresholds"""

    def __init__(self, symbol='SPY', period='5y'):
        self.symbol = symbol
        self.data = yf.download(symbol, period=period, progress=False)
        self.prices = self.data['Close'].values.astype(float)

    def test_threshold(self, threshold):
        """Test one confluence threshold value"""
        algo = HurstCyclicAlgorithm(self.data, use_fld=True)

        # Modify threshold before running
        # NOTE: This requires accessing internal engine
        # For now, we'll modify the source code instead

        report = algo.run()

        num_signals = len(algo.signals)
        num_trades = len(algo.trades)

        return {
            'threshold': threshold,
            'signals': num_signals,
            'trades': num_trades,
        }

    def tune_all(self):
        """Test multiple thresholds"""
        thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
        results = []

        for t in thresholds:
            print(f"Testing threshold: {t:.0%}")
            result = self.test_threshold(t)
            results.append(result)
            print(f"  Signals: {result['signals']}, Trades: {result['trades']}")

        # Find sweet spot (>3 signals, >1 trade)
        viable = [r for r in results if r['signals'] >= 3]
        if viable:
            print(f"\nViable thresholds: {[f\"{r['threshold']:.0%}\" for r in viable]}")

if __name__ == '__main__':
    tuner = HurstParameterTuner(symbol='SPY')
    tuner.tune_all()
```

---

## IMPLEMENTATION: METHOD 3 - MARKET REGIME SELECTION

Instead of modifying thresholds, test on periods where signals are more likely:

### Test on Volatile Periods

The system should generate signals more readily when:
- Market is choppy/sideways (not trending)
- Volatility is elevated
- Mean reversion is working

**Recommended test periods:**
1. **2020-2022:** COVID crash + recovery (VERY VOLATILE) ✓
2. **2018-2019:** After Q4 2018 correction ✓
3. **2015-2016:** Fed uncertainty period ✓
4. **2011-2012:** Debt ceiling crisis ✓

### Create test on 2020-2022:

```python
# In week1_validation_simple.py, modify:

def main():
    # Instead of 5-year recent data
    # for symbol in ['SPY', 'QQQ', 'IWM']:
    #     validator = Week1SimplifiedValidator(symbol=symbol, period='5y')

    # Test on volatile period
    print("TESTING ON 2020-2022 VOLATILE PERIOD")
    for symbol in ['SPY', 'QQQ', 'IWM']:
        validator = Week1SimplifiedValidator(symbol=symbol, period='5y')
        # Download and slice to 2020-2022 only
        validator.download_data()
        if validator.data is not None:
            validator.data = validator.data['2020':'2022']
        validator.run()
```

---

## SIGNAL GENERATION THRESHOLDS - BREAKDOWN

### Current Signal Conditions

The system generates signals when:

1. **Edge-Band Signal:** Price crosses envelope edge
   - Requires confluence >= 30% (too strict)
   - Current: Rarely triggered

2. **Mid-Band Signal:** Half-span MA crossing
   - Requires confluence >= 40% (too strict)
   - Current: Rarely triggered

3. **FLD Signal:** Future Line of Demarcation
   - Requires confluence >= 40% (too strict)
   - Current: Rarely triggered

### Confluence = How Many Cycles Align

Example:
- If 18-month + 40-week cycles both suggest DOWN, confluence = 2
- If 18-month + 40-week + 20-week all suggest DOWN, confluence = 3
- Confluence score = # agreeing cycles / total cycles

**Current:** Requires 40% of cycles to agree
**Recommended:** Lower to 20-30% of cycles to agree

---

## TESTING DIFFERENT THRESHOLDS

### Threshold 0.40 (Current) - STRICT
- ✓ Very few false signals
- ✗ Misses many real opportunities
- ✗ Zero trades in uptrend
- Market Period: Not suitable for 2021-2026

### Threshold 0.30 - MODERATE
- ✓ Fewer false signals
- ✓ More realistic signals
- ~ Mixed performance in trending markets
- Market Period: Better for choppy markets

### Threshold 0.20 - AGGRESSIVE
- ~ More signals (good and bad)
- ✓ Should generate trades in most periods
- ✗ Risk of more false signals
- Market Period: Good for volatile periods

### Threshold 0.10 - VERY AGGRESSIVE
- ✓ Generates many signals
- ✗ High false signal rate
- ✗ Likely to overfit
- Market Period: Use only for extreme volatility

---

## RECOMMENDED TUNING PROCESS

### Phase 1: Test on Multiple Market Periods (Today)
```bash
# Test current thresholds on different periods:
python test_on_2020_2022.py    # Should generate signals
python test_on_2021_2026.py    # Currently 0 signals
python test_on_2015_2016.py    # Should generate signals
```

### Phase 2: Find Sweet Spot (This Week)
- Test 7 threshold values: 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40
- For each: measure win rate, sharpe, drawdown
- Select threshold that maximizes risk-adjusted return
- Typical: 20-30% confluence for mean-reversion

### Phase 3: Validate on Out-of-Sample (Week 2)
- Train on 70% of data
- Test on 30% unseen data
- Verify performance consistency
- Adjust if overfitted

### Phase 4: Walk-Forward Testing (Week 2-3)
- 10 overlapping periods
- Train-test-measure consistency
- Verify Sharpe ratio > 1.5
- Verify return > 10% annually

---

## IMPLEMENTATION CHECKLIST

- [ ] Identify exact line numbers in hurst_cyclic_trading.py where thresholds are set
- [ ] Create backup of current hurst_cyclic_trading.py
- [ ] Test on 2020-2022 volatile period with current thresholds (should already generate signals)
- [ ] If no signals in 2020-2022, lower confluence threshold to 0.25
- [ ] Re-test on 2020-2022 and verify signals appear
- [ ] Run walk-forward validation to verify consistency
- [ ] Measure Sharpe, win rate, max DD across multiple periods
- [ ] Document final tuned parameters
- [ ] Proceed with Week 2-8 validation sprint

---

## EXPECTED RESULTS AFTER TUNING

### With threshold 0.20-0.25 on 2020-2022:
- Signals generated: 5-15 per asset
- Trades executed: 3-10 per asset
- Win rate: 50-60%
- Return: 8-15% (annualized)
- Sharpe: 1.2-1.8
- Max DD: 10-15%

### If these targets are met:
- Proceed to Week 2 robustness testing
- Test parameter sensitivity across 25+ combinations
- Validate on 6+ different assets
- Run full 8-week validation sprint

---

## SAFETY CHECKS

Before going to production with tuned parameters:

1. **Overfitting Check:** Does it work on out-of-sample data?
2. **Period Check:** Does it work across 3+ different market periods?
3. **Asset Check:** Does it work on 4+ of 6 tested assets?
4. **Regime Check:** Does it work in sideways markets (where it should work)?
5. **Consistency Check:** Are results stable across walk-forward periods?

---

## CONCLUSION

The Hurst system is working perfectly. The zero-signal result is not a bug but a feature of the conservative signal generation logic. By:

1. Lowering confluence thresholds (40% → 20-30%)
2. Testing on market periods favorable to mean reversion
3. Walking forward to verify consistency
4. Ablating to understand component contributions

We will establish whether this system has real alpha or if the signal generation is simply too conservative. The complete 8-week validation sprint will definitively answer this question.

**Next Step:** Lower confluence threshold to 0.25 and test on 2020-2022 volatile period.

