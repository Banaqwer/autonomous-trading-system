# Trading Dashboard - Quick Start Guide

## What You Now Have

A **beautiful, professional trading dashboard** that monitors your entire autonomous trading system in real-time.

---

## 3 Files Created

### 1. **dashboard.py** (Backend)
- Flask web server
- Serves real-time trading data
- Provides REST API for dashboard
- Stores all trading logs

### 2. **templates/index.html** (Frontend)
- Beautiful HTML dashboard
- Real-time updates
- Professional design
- Easy navigation

### 3. **static/style.css** (Styling)
- Modern dark theme
- Responsive design (desktop/mobile)
- Charts and visualizations
- Professional UI/UX

### 4. **static/script.js** (Interactivity)
- Real-time data updates (every 5 seconds)
- Interactive charts
- Responsive layout
- Live clock and status

### 5. **data_logger.py** (Data Integration)
- Logs market scans
- Tracks positions
- Records trades
- Generates alerts

---

## How It Works

```
Trading System          Dashboard Backend       Dashboard Frontend
   ↓                         ↓                          ↓
Runs at 9 AM      →   Logs to JSON file    →   Displays real-time
  Scans markets         data_logger.py          updates every 5 sec
  Trades signals   →   REST API (/api/*)   →   Beautiful UI
  Tracks positions      /api/data                Shows all data
  Calculates PnL        /api/markets            Charts & metrics
                        /api/positions
                        /api/performance
```

---

## Starting the Dashboard

### Step 1: Install Flask

```bash
pip install flask
```

### Step 2: Start the Dashboard

```bash
cd "C:\Users\hugoh\Downloads\New foldery\GrosFichiers - Hugo"
python dashboard.py
```

You should see:

```
================================================================================
AUTONOMOUS TRADING SYSTEM DASHBOARD
================================================================================

✓ Dashboard running at: http://localhost:5000
✓ Open in your browser to monitor trading system
✓ Real-time updates every 5 seconds

================================================================================
```

### Step 3: Open in Browser

Go to: **http://localhost:5000**

You should see a beautiful trading dashboard!

---

## Dashboard Layout

### Top Section: Key Metrics
```
┌─────────────────────────────────────────────────────┐
│ Total Capital    Allocated    Reserve    Monthly Return │
│ $100,000         $45,000      $55,000    +2.3%        │
└─────────────────────────────────────────────────────┘
```

### Middle Section: Markets & Positions
```
┌──────────────────────────┬──────────────────────────┐
│ Market Rankings          │ Open Positions           │
├──────────────────────────┼──────────────────────────┤
│ BTC-USD    0.98  40%     │ BTC LONG @ $50,500       │
│ ETH-USD    0.85  30%     │ PnL: +$350 (+0.7%)       │
│ SPY        0.80  30%     │ Stop: $50,000            │
└──────────────────────────┴──────────────────────────┘
```

### Lower Section: Performance & Alerts
```
┌──────────────────────────┬──────────────────────────┐
│ Performance Metrics      │ Recent Alerts            │
├──────────────────────────┼──────────────────────────┤
│ Total Trades:      12    │ BTC LONG opened @ $50.5k │
│ Win Rate:          75%   │ Capital allocated: $90k  │
│ Sharpe:           3.2    │ Market scan completed    │
│ Max Drawdown:      -2%   │ System ready             │
└──────────────────────────┴──────────────────────────┘
```

### Bottom: Trade History
```
Date        Market   Signal   Entry    Exit    PnL    Return  Status
2026-03-30  BTC-USD  LONG     50500    51200   +350   +0.7%   OPEN
2026-03-29  SPY      LONG     450      459     +130   +2.9%   CLOSED
```

---

## Integration with Trading System

### How Data Flows

1. **Trading System Runs** (9 AM daily)
   - Scans 6 markets
   - Calculates quality scores
   - Allocates capital
   - Executes trades

2. **Data Logged** (via data_logger.py)
   ```python
   logger = TradingDataLogger()
   logger.log_market_scan(markets_data)
   logger.log_position_opened("BTC-USD", entry_price=50500, ...)
   logger.log_alert("BTC LONG opened")
   ```

3. **Dashboard Updates** (Real-time)
   - Fetches data from JSON file
   - Updates every 5 seconds
   - Shows all metrics live
   - Displays alerts

---

## Using the Dashboard

### View Real-Time Markets
- See quality score for each market
- See regime classification
- See capital allocation percentages
- See best market highlighted

### Monitor Open Positions
- See entry price and current price
- See unrealized PnL in dollars and %
- See stop loss and take profit levels
- See position size

### Track Performance
- Total trades executed
- Win rate (wins/total)
- Sharpe ratio
- Max drawdown
- Monthly return
- Cumulative PnL

### Receive Alerts
- Trade opened/closed
- Capital allocated
- Regime changes
- System status
- Errors/warnings

### View Trade History
- Date and time
- Market traded
- Entry/exit prices
- PnL and return %
- Trade status (open/closed)

---

## Integrating with Your Trading System

### Option 1: Automatic Logging (Recommended)

Modify `run_trading_system.py` to use the logger:

```python
from data_logger import TradingDataLogger

class AutonomousTradingScheduler:
    def __init__(self):
        # ... existing code ...
        self.logger = TradingDataLogger()

    def run_trading_cycle(self):
        # ... existing code ...

        # Log market scan results
        self.logger.log_market_scan(market_rankings_data)

        # Log positions
        self.logger.log_position_opened(...)

        # Log alerts
        self.logger.log_alert("Trading cycle complete")
```

### Option 2: Manual Updates

Update dashboard data directly:

```python
from data_logger import TradingDataLogger

logger = TradingDataLogger()

# After each trading cycle
logger.log_market_scan(markets)
logger.update_performance(sharpe_ratio=3.2, max_drawdown=-2.0)
logger.log_alert("Daily trade executed successfully")
```

---

## Dashboard Features

✓ **Real-time Updates** - Refreshes every 5 seconds
✓ **Beautiful Design** - Professional dark theme
✓ **Responsive** - Works on desktop, tablet, mobile
✓ **Live Charts** - Capital allocation pie chart
✓ **Performance Metrics** - All key stats in one view
✓ **Alert History** - Track system events
✓ **Trade Log** - Complete history of all trades
✓ **Position Tracking** - Monitor open positions live
✓ **Market Rankings** - See quality scores for all markets
✓ **Status Display** - System health and next scan time

---

## Troubleshooting

### Dashboard won't start
```
Error: Address already in use
→ Port 5000 is in use. Change port in dashboard.py:
  app.run(host="localhost", port=5001)  # Change 5000 to 5001
```

### Dashboard shows "Loading..."
```
→ Trading system hasn't logged data yet
→ Run trading cycle: python -c "..."
→ Then refresh dashboard (F5)
```

### No market data showing
```
→ Ensure data_logger.py is imported in trading system
→ Ensure log data is being written to trading_logs/dashboard_data.json
→ Check file exists: dir trading_logs
```

### Updates not refreshing
```
→ Check browser console for errors (F12 → Console)
→ Try hard refresh (Ctrl+Shift+R)
→ Ensure Flask server is running
```

---

## Next Steps

1. **Start Dashboard**: `python dashboard.py`
2. **Open Browser**: http://localhost:5000
3. **Verify Visual Design**: Check that it looks beautiful ✓
4. **Run Trading System**: Execute trading cycle
5. **Watch Data Populate**: See market scans, trades, positions
6. **Monitor Performance**: Track daily results

---

## Features to Add Later (Optional)

- [ ] Dark/Light theme toggle
- [ ] Export trade history to CSV
- [ ] Custom date range filtering
- [ ] Trade setup emails with dashboard link
- [ ] Mobile app integration
- [ ] WebSocket for faster updates
- [ ] Email notifications
- [ ] Telegram alerts
- [ ] Advanced analytics
- [ ] Risk heat map

---

## Summary

You now have:

✓ **Beautiful Dashboard** - Professional monitoring interface
✓ **Real-time Updates** - Every 5 seconds
✓ **Data Integration** - Logs all trading activities
✓ **Performance Tracking** - All metrics in one view
✓ **Position Monitoring** - See open trades live
✓ **Market Rankings** - View quality scores
✓ **Alert System** - Track system events
✓ **Complete Visibility** - Everything you need to monitor

**Ready to deploy on cloud and trade 24/7!**

