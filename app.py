"""
Unified App - Runs Dashboard + Trading Scheduler in one process
Fixes the filesystem sharing issue on Render
"""

import threading
import schedule
import time
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── TRADING SCHEDULER THREAD ──────────────────────────────────────────────────
def run_trading_cycle():
    """Execute one full trading cycle."""
    try:
        logger.info("=" * 60)
        logger.info("TRADING CYCLE STARTED")
        logger.info("=" * 60)

        import yfinance as yf
        import numpy as np
        import pandas as pd
        from data_logger import TradingDataLogger

        data_logger = TradingDataLogger()
        data_logger.update_status("SCANNING", f"Next: {datetime.now().strftime('%H:%M')}")

        MARKETS = ["BTC-USD", "ETH-USD", "SPY", "QQQ", "GLD"]
        fetch_map = {
            "BTC-USD": "BTC-USD",
            "ETH-USD": "ETH-USD",
            "SPY":     "SPY",
            "QQQ":     "QQQ",
            "GLD":     "GLD"
        }

        market_results = []

        for market in MARKETS:
            try:
                ticker = fetch_map.get(market, market)
                logger.info(f"Scanning {market}...")

                df = yf.download(ticker, period="1y", interval="1d", auto_adjust=True)
                if df.empty or len(df) < 50:
                    logger.warning(f"  {market}: insufficient data")
                    continue

                prices = df["Close"].values.flatten()

                # Regime detection
                ema50 = pd.Series(prices).ewm(span=50).mean().values
                slope = (ema50[-1] - ema50[-20]) / ema50[-20] * 100
                pos   = (prices[-1] - ema50[-1]) / ema50[-1] * 100

                if   slope > 1.5 and pos > 1.0:  regime, regime_score = "STRONG_UPTREND",  1.0
                elif slope > 0.5 and pos > 0:    regime, regime_score = "UPTREND",          0.7
                elif slope < -1.5:               regime, regime_score = "STRONG_DOWNTREND", 0.0
                elif slope < -0.5:               regime, regime_score = "DOWNTREND",         0.1
                else:                            regime, regime_score = "NEUTRAL",           0.3

                # Trend strength
                returns = pd.Series(prices).pct_change().dropna()
                trend_strength = min(abs(slope) / 3.0, 1.0)
                confidence     = regime_score
                position_size  = regime_score

                quality_score = (trend_strength * 0.5) + (confidence * 0.3) + (position_size * 0.2)

                market_results.append({
                    "symbol":        market,
                    "regime":        regime,
                    "quality_score": round(quality_score, 4),
                    "trend_strength":round(trend_strength, 4),
                    "confidence":    round(confidence, 4),
                    "position_size": round(position_size, 4),
                    "current_price": round(float(prices[-1]), 2),
                    "change_pct":    round(float((prices[-1]/prices[-2]-1)*100), 2)
                })

                logger.info(f"  {market}: {regime} | Score: {quality_score:.3f}")

            except Exception as e:
                logger.error(f"  {market} error: {e}")

        if market_results:
            best = max(market_results, key=lambda x: x["quality_score"])
            data_logger.log_market_scan(market_results, best["symbol"])
            data_logger.update_status("READY", f"Next: 09:00 AM EST")
            logger.info(f"Best market: {best['symbol']} (score: {best['quality_score']:.3f})")
        else:
            data_logger.update_status("READY", "Next: 09:00 AM EST")
            logger.warning("No markets passed quality threshold")

        logger.info("TRADING CYCLE COMPLETE")

    except Exception as e:
        logger.error(f"Trading cycle error: {e}")

def run_scheduler():
    """Background thread that runs trading on schedule."""
    logger.info("Scheduler started — trading at 14:00 UTC (9 AM EST) daily")
    schedule.every().day.at("14:00").do(run_trading_cycle)

    # Also run once on startup after 30 seconds
    def startup_run():
        time.sleep(30)
        logger.info("Running startup scan...")
        run_trading_cycle()

    startup_thread = threading.Thread(target=startup_run, daemon=True)
    startup_thread.start()

    while True:
        schedule.run_pending()
        time.sleep(60)

# ── START SCHEDULER IN BACKGROUND ─────────────────────────────────────────────
scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()
logger.info("Background scheduler started")

# ── FLASK DASHBOARD ───────────────────────────────────────────────────────────
from flask import Flask, render_template, jsonify
import json
from pathlib import Path

app = Flask(__name__)
LOGS_DIR  = Path("trading_logs")
DATA_FILE = LOGS_DIR / "dashboard_data.json"
LOGS_DIR.mkdir(exist_ok=True)

DEFAULT_DATA = {
    "status": "READY",
    "next_scan": "09:30 AM",
    "total_capital": 100000,
    "allocated_capital": 0,
    "reserve_capital": 100000,
    "monthly_return": 0.0,
    "markets": [],
    "positions": [],
    "trades": [],
    "performance": {"sharpe_ratio": 0, "max_drawdown": 0, "total_return": 0,
                    "win_rate": 0, "total_trades": 0, "avg_win": 0, "avg_loss": 0},
    "alerts": [],
    "last_updated": datetime.now().isoformat()
}

def load_data():
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return DEFAULT_DATA

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    return jsonify(load_data())

@app.route('/api/markets')
def api_markets():
    return jsonify(load_data().get('markets', []))

@app.route('/api/positions')
def api_positions():
    return jsonify(load_data().get('positions', []))

@app.route('/api/performance')
def api_performance():
    return jsonify(load_data().get('performance', {}))

@app.route('/api/status')
def api_status():
    d = load_data()
    return jsonify({"status": d.get("status", "READY"),
                    "next_scan": d.get("next_scan", "09:30 AM"),
                    "last_updated": d.get("last_updated", "")})

@app.route('/api/trigger_scan')
def trigger_scan():
    """Manually trigger a trading scan."""
    t = threading.Thread(target=run_trading_cycle, daemon=True)
    t.start()
    return jsonify({"message": "Scan triggered", "time": datetime.now().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
