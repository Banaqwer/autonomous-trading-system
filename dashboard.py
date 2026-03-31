"""
Autonomous Trading System Dashboard
Beautiful, real-time monitoring interface
"""

from flask import Flask, render_template, jsonify
import json
from datetime import datetime
import os
from pathlib import Path

app = Flask(__name__)

# Data storage paths
LOGS_DIR = Path("trading_logs")
DATA_FILE = LOGS_DIR / "dashboard_data.json"
LOGS_DIR.mkdir(exist_ok=True)

# Default dashboard data structure
DEFAULT_DATA = {
    "system_status": "READY",
    "last_scan": None,
    "next_scan": "09:00 AM",
    "markets": [
        {"symbol": "BTC-USD", "regime": "SCANNING...", "quality": 0, "allocation": 0},
        {"symbol": "ETH-USD", "regime": "SCANNING...", "quality": 0, "allocation": 0},
        {"symbol": "SPY", "regime": "SCANNING...", "quality": 0, "allocation": 0},
        {"symbol": "QQQ", "regime": "SCANNING...", "quality": 0, "allocation": 0},
        {"symbol": "EUR/USD", "regime": "SCANNING...", "quality": 0, "allocation": 0},
        {"symbol": "GLD", "regime": "SCANNING...", "quality": 0, "allocation": 0},
    ],
    "positions": [],
    "performance": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0,
        "monthly_return": 0,
        "monthly_pnl": 0,
        "sharpe_ratio": 0,
        "max_drawdown": 0,
    },
    "alerts": [],
    "capital": {"total": 100000, "allocated": 0, "reserve": 0},
}


def load_data():
    """Load dashboard data from file."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            return DEFAULT_DATA.copy()
    return DEFAULT_DATA.copy()


def save_data(data):
    """Save dashboard data to file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


@app.route("/")
def index():
    """Main dashboard page."""
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    """Get all dashboard data."""
    data = load_data()
    return jsonify(data)


@app.route("/api/markets")
def get_markets():
    """Get market data only."""
    data = load_data()
    return jsonify(data["markets"])


@app.route("/api/positions")
def get_positions():
    """Get open positions."""
    data = load_data()
    return jsonify(data["positions"])


@app.route("/api/performance")
def get_performance():
    """Get performance metrics."""
    data = load_data()
    return jsonify(data["performance"])


@app.route("/api/status")
def get_status():
    """Get system status."""
    data = load_data()
    return jsonify({
        "status": data["system_status"],
        "last_scan": data["last_scan"],
        "next_scan": data["next_scan"],
        "alerts": data["alerts"][-5:],  # Last 5 alerts
    })


@app.route("/api/update", methods=["POST"])
def update_data():
    """Update dashboard data (called by trading system)."""
    import request
    data = load_data()
    update = request.get_json()

    # Merge update with existing data
    for key, value in update.items():
        if isinstance(value, dict) and key in data:
            data[key].update(value)
        else:
            data[key] = value

    save_data(data)
    return jsonify({"status": "updated"})


if __name__ == "__main__":
    print("\n" + "="*80)
    print("AUTONOMOUS TRADING SYSTEM DASHBOARD")
    print("="*80)
    print("\n✓ Dashboard running at: http://localhost:5000")
    print("✓ Open in your browser to monitor trading system")
    print("✓ Real-time updates every 5 seconds")
    print("\n" + "="*80 + "\n")

    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
