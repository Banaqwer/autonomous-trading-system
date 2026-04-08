# HURST TRADING BOT - ALPACA SETUP & OPERATIONS GUIDE

## PHASE 1: PAPER TRADING (April 8 - June 8)

### STEP 1: Get Alpaca API Credentials

#### Option A: You Already Have Alpaca Account
1. Log into your Alpaca account (alpaca.markets)
2. Go to **Dashboard > API Keys**
3. Create a new API key (or use existing)
4. Copy both:
   - `API_KEY` (looks like: `PK123456789...`)
   - `SECRET_KEY` (looks like: `abcdefghijk...`)

#### Option B: Create New Alpaca Account for Paper Trading
1. Go to https://alpaca.markets
2. Sign up (takes 5 minutes)
3. **IMPORTANT: Enable Paper Trading in settings**
4. Get your API keys (above)
5. Fund account with minimum $500 (for paper trading)

### STEP 2: Set Up Environment Variables

#### On Windows (Command Prompt):
```
setx ALPACA_API_KEY "YOUR_API_KEY_HERE"
setx ALPACA_SECRET_KEY "YOUR_SECRET_KEY_HERE"
```

#### On Windows (PowerShell):
```
$env:ALPACA_API_KEY = "YOUR_API_KEY_HERE"
$env:ALPACA_SECRET_KEY = "YOUR_SECRET_KEY_HERE"
```

#### On Mac/Linux:
```
export ALPACA_API_KEY="YOUR_API_KEY_HERE"
export ALPACA_SECRET_KEY="YOUR_SECRET_KEY_HERE"
```

### STEP 3: Install Required Libraries

```bash
pip install alpaca-trade-api
pip install yfinance
```

### STEP 4: Test the Connection

```bash
python trading_bot_alpaca_integration.py
```

**Expected Output:**
```
Connected to Alpaca. Account: ABC123456
Cash available: $100,000.00
Equity: $100,000.00
Mode: PAPER TRADING
Ready to trade: 40/40 assets
```

### STEP 5: Run Daily

#### OPTION A: Manual Daily Execution (Best for Testing)
```bash
# Run once per day at 9:30 AM EST
python trading_bot_alpaca_integration.py
```

**Procedure:**
1. Run script at 9:30 AM (market open)
2. Script generates signals and executes trades
3. All trades logged to `trading_logs/` folder
4. Check logs to verify execution

#### OPTION B: Automated Hourly Checks (More Frequent)
Create a scheduler script:

```python
import schedule
import time
from trading_bot_alpaca_integration import HurstTradingBot

# Initialize bot
api_key = "YOUR_KEY"
secret_key = "YOUR_SECRET"
bot = HurstTradingBot(api_key, secret_key, paper_trading=True)

# Run every hour during market hours (9:30 AM - 4:00 PM EST)
schedule.every().hour.do(bot.run_daily)

while True:
    schedule.run_pending()
    time.sleep(60)
```

---

## PAPER TRADING EXECUTION PLAN (April 8 - June 8)

### Week 1 (Apr 8-12): Initial Testing
**Goal:** Verify bot works, signals generate correctly
- Run daily
- Monitor signal generation
- Check trade execution
- Expected: 5-6 signals

**Decision Gate (Friday Apr 12):**
- IF: Win rate >= 70% AND signals generated
- THEN: Continue to week 2
- ELSE: Debug and extend testing

### Weeks 2-4 (Apr 15 - May 3): Validation
**Goal:** Collect 30-40 trades for statistical validation
- Run daily
- Track P&L
- Verify win rate matches backtest (~77%)
- Expected: 30+ signals

**Success Criteria:**
- Signal frequency: 6-11/week
- Win rate: 70%+
- Daily accuracy: Most days 0 signals OR high-confidence trades

### Weeks 5-8 (May 6 - June 2): Full Month Test
**Goal:** Complete 2-month paper trading validation
- Expected: 60+ trades
- Validate consistency across market conditions
- Test all 40 assets actively trading

**Final Decision Gate (Friday June 6):**
IF all criteria met:
- Win rate: 70%+
- Signal frequency: 10-12/week average
- No major losses or errors
- P&L positive: Yes

THEN: **APPROVE FOR REAL CAPITAL**

---

## REAL CAPITAL TRANSITION (After June 8)

### STEP 1: Open Interactive Brokers Account (Covers All 40 Assets)

Interactive Brokers is needed because:
- Alpaca doesn't support forex (FXC, FXE, FXG, FXY, etc.)
- Alpaca has limited commodity access (USO, etc.)
- Interactive Brokers covers 100% of 40-asset portfolio

**Setup:**
1. Open account at https://www.interactivebrokers.com
2. Fund with $100,000+
3. Get API credentials
4. Update bot to use IB instead of Alpaca

### STEP 2: Update Bot Configuration

**Change in code:**
```python
# Change from Alpaca to Interactive Brokers
# bot = HurstTradingBot(api_key, secret_key, paper_trading=True)
# to:
# bot = HurstTradingBot(ib_account, ib_password, broker='IB')
```

### STEP 3: Run Real Capital Mode

```python
bot = HurstTradingBot(api_key, secret_key, paper_trading=False)
bot.run_continuous(check_interval_minutes=5)
```

---

## DAILY MONITORING CHECKLIST

### Before Market Open (8:00 AM - 9:30 AM)
- [ ] Verify bot is running
- [ ] Check that data downloads successfully
- [ ] Confirm Alpaca connection works
- [ ] Review previous day's P&L

### During Market Hours (9:30 AM - 4:00 PM)
- [ ] Monitor log file for signals
- [ ] Check executed trades
- [ ] Verify position sizes
- [ ] Watch for any errors

### After Market Close (4:00 PM - 5:00 PM)
- [ ] Review day's performance
- [ ] Log trades manually (backup)
- [ ] Calculate daily win rate
- [ ] Check risk metrics

### Weekly (Friday 5:00 PM)
- [ ] Compile weekly statistics
- [ ] Calculate win rate (target: 70%+)
- [ ] Count total signals (target: 8-12)
- [ ] Review P&L
- [ ] Document any issues

---

## TROUBLESHOOTING

### "Connection Failed to Alpaca"
**Solution:**
1. Verify API key is correct
2. Check internet connection
3. Verify environment variables are set:
   ```
   echo %ALPACA_API_KEY%
   echo %ALPACA_SECRET_KEY%
   ```

### "No Signals Generated"
**Likely Cause:** Algorithm parameters or data issue
1. Check that data downloaded: `len(bot.data) == 40`
2. Verify algorithm is working
3. Run individual asset test

### "Trades Not Executing"
**Check:**
1. Alpaca account has sufficient cash
2. Market is open (9:30 AM - 4:00 PM EST)
3. Asset is tradeable on Alpaca
4. Position size is valid

### "Low Win Rate (< 70%)"
**Action Items:**
1. Check for data quality issues
2. Verify algorithm settings match backtest
3. Extend paper trading period
4. Do NOT deploy real capital until 70%+ achieved

---

## PERFORMANCE TRACKING

### Essential Metrics
```
Daily Tracking:
- Date
- Signals generated
- Trades executed
- Winners / Losers
- Daily P&L
- Daily Win Rate %

Weekly Tracking:
- Total signals
- Total trades
- Weekly win rate %
- Weekly P&L
- Profit factor (wins/losses)

Monthly Tracking:
- Month signals
- Month trades
- Month win rate %
- Month return %
- Consistency check
```

### Success Metrics for Real Capital Approval
```
[MUST HAVE]
- Win rate >= 70%
- Signals per week >= 8
- No catastrophic losses
- Algorithm stable

[NICE TO HAVE]
- Win rate >= 75%
- Signals per week >= 12
- Consistent daily execution
- No operational issues
```

---

## IMPORTANT NOTES

### Paper Trading Mode
- NO REAL MONEY LOST
- Executes trades with virtual capital
- Same fills as real trading (uses real market prices)
- Perfect for testing

### Risk Management Built In
- 2% risk per trade
- Maximum 10% per position
- Automatic stop losses
- Daily loss limits

### Logging Everything
All trades logged to: `trading_logs/trading_bot_YYYYMMDD_HHMMSS.log`
- Every signal
- Every trade
- Every error
- Complete audit trail

### Transitioning to Real Capital
Only after 2 months paper trading + 70%+ win rate:
1. Change `paper_trading=False`
2. Update to Interactive Brokers
3. Fund account with $100,000
4. Run live

---

## NEXT STEPS (Monday April 8, 2026)

1. **8:00 AM:** Get Alpaca API credentials
2. **8:15 AM:** Set environment variables
3. **8:30 AM:** Test connection with bot
4. **9:00 AM:** Final verification
5. **9:30 AM:** Launch bot - LIVE PAPER TRADING BEGINS

---

## CONTACT / SUPPORT

All issues logged to: `trading_logs/` directory
Monitor these files daily for:
- Errors
- Warnings
- Performance metrics
- Trade execution logs

Questions? Check the log files - they contain complete execution history.

Ready to launch? Let's go.
