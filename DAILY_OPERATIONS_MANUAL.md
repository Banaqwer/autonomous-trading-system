# DAILY OPERATIONS MANUAL
## Optimization Program - Tactical Execution Guide
### April 6, 2026 - Ongoing

---

## **DAILY MONITORING CHECKLIST**

### Every Trading Day

**Morning (Before Market Open)**
- [ ] Check system health (all connections working)
- [ ] Review overnight news/events (economic calendar)
- [ ] Assess market regime (uptrend/downtrend/sideways)
- [ ] Confirm position limits (max 30% per asset)
- [ ] Verify margin buffer (40%+ required)

**During Market Hours**
- [ ] Monitor active signals generation
- [ ] Watch win rate (should be 65%+)
- [ ] Track open P&L
- [ ] Verify stops are working
- [ ] Check for execution issues

**Market Close**
- [ ] Close out any day trades
- [ ] Log all trades (for analysis)
- [ ] Record daily P&L
- [ ] Check overnight gap risk
- [ ] Update KPI tracking

**Evening (After Close)**
- [ ] Analyze day's trades
- [ ] Update monthly metrics
- [ ] Review any alerts
- [ ] Document decisions
- [ ] Prepare next day

---

## **WEEKLY METRICS REVIEW**

Every Friday EOD:

```
WIN RATE:          Target 65%+, Alert if < 60%
MONTHLY RETURN:    Target 2-5%, Alert if < 1%
MAX WEEKLY DD:     Target < 3%, Alert if > 5%
CAPITAL GROWTH:    Track weekly increase
SHARPE RATIO:      Target 1.0+, Alert if < 0.5
TRADES:            Count and categorize
LARGEST WIN:       Document
LARGEST LOSS:      Review for improvement
```

---

## **PHASE 1A: WEEKS 1-3 DAILY SCHEDULE**

### Week 1: Baseline Validation (Paper Trading)

**Monday (Apr 8)**
- Set up paper trading account
- Deploy Phase 1A code (2% risk)
- Run morning signals
- Confirm execution logic working
- Document system status

**Tuesday-Thursday (Apr 9-11)**
- Run daily validation backtests
- Confirm signal generation (>2-3 signals/day on live data)
- Verify stop-loss placement
- Check P&L calculation
- Document any issues

**Friday (Apr 12)**
- Weekly review of validation
- Win rate assessment (need >65%)
- Execution quality review
- Decision: GO to Phase 1B or retry 1A

**Weekend**
- Backtest historical data
- Verify parameter stability
- Prepare for Phase 1B launch

---

### Week 2-3: Phase 1B Launch (Conservative Kelly - 5% Risk)

**Monday (Apr 15)**
- Launch real trading with Phase 1B parameters
- Initial capital: $100,000
- Risk per trade: 5% (2.5x baseline)
- Daily monitoring begins

**Tuesday-Friday (Apr 16-19)**
- Daily trade execution
- Monitor win rate (target 65%+)
- Track monthly return (target 2-3% weekly)
- Maximum monthly drawdown < 5%
- Alert if any red flags

**Friday EOW (Apr 19)**
- Weekly metrics: Win rate, return, DD
- Performance vs baseline
- Decision: On track for week 4?

---

## **CRITICAL ALERTS & RESPONSES**

### Alert Level 1: YELLOW (Caution)

**Triggered By:**
- Win rate 60-64% (below ideal but acceptable)
- Weekly return < 1%
- Single position > 15% of account
- Monthly DD 3-5%

**Response:**
- Reduce new position sizes 20%
- Increase confluence threshold 0.02
- Monitor closely next week
- Review signal quality

---

### Alert Level 2: ORANGE (Warning)

**Triggered By:**
- Win rate 55-59% (concerning)
- Weekly return negative
- Single position > 20%
- Monthly DD 5-8%
- 2 consecutive losing trades

**Response:**
- Reduce position sizes 50%
- Increase confluence threshold 0.05
- Halt new trades temporarily
- Investigate root cause
- Report to Head of Direction

---

### Alert Level 3: RED (Critical)

**Triggered By:**
- Win rate < 55% (critical failure)
- Monthly return < -3%
- Account drawdown > 8%
- System execution errors
- 3+ consecutive losing trades

**Response:**
- **HALT ALL TRADING IMMEDIATELY**
- Reduce position sizes to 25% of normal
- Full system diagnostic
- Investigation mode for 24-48 hours
- Do not resume without approval

---

## **POSITION SIZING EXECUTION**

### Kelly Sizing Formula (Phase 1B: 5% Risk)

```
Risk Per Trade = 5% of Account
Position Size = (Account × Risk%) / Stop-Loss Distance

Example:
Account = $100,000
Risk = 5% = $5,000
Stock price = $100
Hurst envelope stop = 2% = $2
Position size = $5,000 / $2 = 2,500 shares
Actual risk = 2,500 × $2 = $5,000 (5% of account)

Validation: Never risk more than 5% per trade
```

### Multi-Asset Allocation (Phase 2)

```
IWM:  35% of capital = $35,000 on $100k
EEM:  35% of capital = $35,000 on $100k
GLD:  20% of capital = $20,000 on $100k
SPY:   5% of capital = $5,000 on $100k
TLT:   5% of capital = $5,000 on $100k

This is the baseline. Individual positions cannot exceed:
- Single IWM trade: $2,500 (5% of $50k if 2 concurrent)
- Single EEM trade: $2,500 (5% of $50k if 2 concurrent)
- Single GLD trade: $1,500 (5% of $30k)
```

---

## **SIGNAL GENERATION & ENTRY**

### Standard Signal Entry Sequence

**1. Signal Detection**
```
Check Hurst confluence > 0.20 (threshold)
Check spectral strength > 0.01
Confirm FLD signal generation
Verify no hard stops at confluence level
```

**2. Entry Confirmation**
```
Limit order 1-2 ticks below signal price
Wait for fill (max 5 minutes)
If no fill, adjust limit order
Max wait time: 15 minutes
```

**3. Risk Calculation**
```
Entry price: $X
Stop price: X ± Envelope width
Risk dollars: Position size × (Entry - Stop)
Verify risk = 5% of account (Phase 1B)
```

**4. Order Placement**
```
BUY/SELL limit order at signal price
Stop-loss order at envelope boundary
Target order (optional) 2-3R above entry
Trail stop (optional) 0.5R behind market
```

---

## **EXIT & PROFIT TAKING**

### Exit Rules

**Primary (Automatic):**
- FLD reversal signal (primary exit)
- Stop loss hit (automatic)
- Time-based (hold max 5 weeks)

**Secondary (Discretionary):**
- Profit target hit (take 50% off)
- Break-even stops after +1R profit
- Trail stop at +2R profit

**Example Exit Sequence:**
```
Entry: $100 (risk $5)
Stop: $95

Exit Levels:
+$5 (1R profit): Place trailing stop 1% below
+$10 (2R profit): Trail stop 2% below, hold for more
+$15+ (3R profit): Trail stop 3% below, trail higher

OR: Exit on FLD signal, whichever comes first
```

---

## **MONTHLY REPORTING STRUCTURE**

### End of Month Report (Due Last Day)

**Performance Metrics:**
```
Total Trades:          [X]
Winning Trades:        [Y] (Win Rate: Y/X %)
Losing Trades:         [Z]
Avg Winner:            $[A]
Avg Loser:             $[B]
Profit Factor:         [A/B]
Total P&L:             $[C]
Return %:              C / Starting Capital %
Sharpe Ratio:          [X]
Max DD:                [X]%
```

**Capital Tracking:**
```
Starting Capital:      $[X]
Deposits:              $[X]
Withdrawals:           $[X]
Profit/Loss:           $[X]
Ending Capital:        $[X]
Monthly Growth:        %
YTD Growth:            %
```

**Operational Notes:**
```
Issues Encountered:    [List]
Resolutions Applied:   [List]
Parameter Changes:     [List with reasons]
Next Month Goals:      [Clear targets]
```

---

## **PHASE TRANSITIONS CHECKLIST**

### GO/NO-GO: Phase 1B → Phase 2 (After Week 6)

**Performance Metrics (Must All PASS):**
- [ ] Win rate >= 65%
- [ ] Monthly return >= 2%
- [ ] Max DD <= 5%
- [ ] No execution errors
- [ ] Capital >= $110k (10% growth)

**System Health (Must All PASS):**
- [ ] No connection issues
- [ ] All signals generate correctly
- [ ] Stops execute properly
- [ ] P&L calculates accurately
- [ ] Risk limits enforced

**Decision:**
- If ALL pass: **GO** to Phase 2 (Asset Concentration)
- If ANY fail: **NO-GO** - Fix issues, retry next week

---

## **LEVERAGE DEPLOYMENT (Phase 3)**

### Margin Application (After 6-Week Track Record)

**Requirements:**
- Win rate 65%+ sustained
- Monthly return positive 6+ weeks
- Max DD never exceeded 5%
- All systems working perfectly

**Leverage Rules:**
```
Available Margin:     40% of account
Start with:           25% margin (1.25:1 leverage)
Increase to:          50% margin (1.5:1 leverage) if performing

Safety Limits:
- Always keep 40% cash buffer
- Never go below -10% drawdown
- Max leverage 1.5:1
- Never use full available margin
```

---

## **EMERGENCY PROCEDURES**

### If Market Crashes (Sudden -5%+ Move)

1. Check all stop orders activated
2. Verify positions liquidating
3. Monitor margin levels
4. Be ready to close positions manually if needed
5. Maintain 40% margin buffer

### If Broker Connection Lost

1. Switch to backup broker immediately
2. Get account status from broker
3. Manually verify open positions
4. Manually place protective stops if needed
5. Document incident

### If System Malfunction

1. Stop all new trades immediately
2. Manually place stops on all positions
3. Investigate root cause
4. Do not resume until 100% confident
5. Document thoroughly

---

## **DOCUMENTATION REQUIREMENTS**

### Every Trade Must Document:
```
Date/Time:       YYYY-MM-DD HH:MM
Symbol:          IWM / EEM / GLD
Direction:       BUY / SELL
Entry Price:     $XXX.XX
Stop Price:      $XXX.XX
Position Size:   XXX shares
Risk:            $X,XXX (% of account)
Exit Price:      $XXX.XX
Exit Reason:     FLD / Stop / Profit / Time
P&L:             $+/- XXX
R-Multiple:      +/-X.X R
Win/Loss:        WIN / LOSS
Notes:           [Any relevant observations]
```

---

## **SUCCESS METRICS: HOW WE WIN**

### Daily Wins
- Generate 2-5 signals/day
- Execute 80%+ of signals
- Maintain 2:1 win/loss ratio

### Weekly Wins
- Win rate > 65%
- Return > 0.5% (positive)
- Max DD < 3%

### Monthly Wins
- Win rate > 65% sustained
- Return > 2%
- Max DD < 5%
- Capital growth > 10%

### Quarterly Wins
- Win rate > 70%
- Return > 15%
- Capital doubles
- Ready for next phase

---

## **FINAL REMINDERS**

### As Head of Direction, I Will:

✅ **Execute with discipline**
- Follow the plan exactly
- No ad-hoc deviations
- Stick to position sizes

✅ **Monitor relentlessly**
- Daily KPI checks
- Weekly detailed review
- Monthly comprehensive analysis

✅ **Protect capital**
- Enforce all hard stops
- Maintain margin buffers
- Never override risk limits

✅ **Document everything**
- Every trade recorded
- Every decision documented
- Full audit trail maintained

✅ **Make clear decisions**
- GO/NO-GO at phase transitions
- Scale capital based on performance
- Halt trading if risks exceed limits

---

**Execution Begins: April 6, 2026**
**Daily Operations commence immediately**
**Updates: Daily monitoring, Weekly reporting, Monthly analysis**

**Let's execute flawlessly and build wealth** 💰

