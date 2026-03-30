"""
Trading Data Logger
Logs all trading activities to dashboard data file
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

class TradingDataLogger:
    """Logs trading system data for dashboard display."""

    def __init__(self, log_dir: str = "trading_logs"):
        """Initialize logger."""
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.data_file = self.log_dir / "dashboard_data.json"
        self.ensure_data_file()

    def ensure_data_file(self):
        """Ensure dashboard data file exists."""
        if not self.data_file.exists():
            self.initialize_data()

    def initialize_data(self):
        """Initialize dashboard data file."""
        data = {
            "system_status": "READY",
            "last_scan": None,
            "next_scan": "09:00 AM",
            "markets": [],
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
            "capital": {"total": 100000, "allocated": 0, "reserve": 100000},
        }
        self.save_data(data)

    def load_data(self) -> Dict:
        """Load current dashboard data."""
        try:
            with open(self.data_file, "r") as f:
                return json.load(f)
        except:
            self.initialize_data()
            return self.load_data()

    def save_data(self, data: Dict):
        """Save dashboard data."""
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)

    # ==================== MARKET DATA ====================

    def log_market_scan(self, markets: List[Dict], best_market: str = None):
        """
        Log market scan results.

        Args:
            markets: List of market dicts with symbol, regime, quality, allocation
            best_market: Symbol of best market
        """
        data = self.load_data()

        # Update markets
        data["markets"] = markets
        data["last_scan"] = datetime.now().isoformat()

        # Update capital allocation
        total_allocated = sum(m.get("allocation", 0) for m in markets)
        data["capital"]["allocated"] = total_allocated
        data["capital"]["reserve"] = data["capital"]["total"] - total_allocated

        # Update status
        data["system_status"] = "READY"

        self.save_data(data)
        print(f"✓ Logged market scan: {len(markets)} markets scanned")

    # ==================== POSITIONS ====================

    def log_position_opened(
        self,
        symbol: str,
        timeframe: str = "daily",
        trade_type: str = "LONG",
        entry_price: float = 0,
        quantity: float = 0,
        stop_loss: float = 0,
        take_profit: float = 0,
    ):
        """Log new position opened."""
        data = self.load_data()

        position = {
            "symbol": symbol,
            "timeframe": timeframe,
            "type": trade_type,
            "entry_price": entry_price,
            "current_price": entry_price,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "pnl": 0,
            "pnl_pct": 0,
            "opened_at": datetime.now().isoformat(),
        }

        data["positions"].append(position)
        self.save_data(data)

        # Log alert
        self.log_alert(
            f"{symbol} {trade_type} opened @ ${entry_price:.2f}",
            alert_type="info"
        )

        print(f"✓ Logged position: {symbol} {trade_type}")

    def log_position_closed(self, symbol: str, exit_price: float, pnl: float):
        """Log position closed."""
        data = self.load_data()

        for pos in data["positions"]:
            if pos["symbol"] == symbol:
                pos["current_price"] = exit_price
                pnl_pct = (pnl / (pos["entry_price"] * pos["quantity"])) * 100 if pos["entry_price"] else 0
                pos["pnl"] = pnl
                pos["pnl_pct"] = pnl_pct
                pos["closed_at"] = datetime.now().isoformat()
                break

        # Remove closed positions from active list
        data["positions"] = [p for p in data["positions"] if "closed_at" not in p]

        self.save_data(data)

        # Log alert
        status = "PROFIT" if pnl > 0 else "LOSS"
        self.log_alert(
            f"{symbol} closed {status}: ${pnl:.0f} ({pnl_pct:.1f}%)",
            alert_type="info" if pnl > 0 else "warning"
        )

        print(f"✓ Logged closed position: {symbol}")

    def update_position_pnl(self, symbol: str, current_price: float):
        """Update position unrealized PnL."""
        data = self.load_data()

        for pos in data["positions"]:
            if pos["symbol"] == symbol:
                pos["current_price"] = current_price
                pos["pnl"] = (current_price - pos["entry_price"]) * pos["quantity"]
                pos["pnl_pct"] = ((current_price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] else 0
                break

        self.save_data(data)

    # ==================== PERFORMANCE ====================

    def log_trade_result(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        trade_type: str = "LONG",
    ):
        """Log completed trade for performance tracking."""
        data = self.load_data()

        perf = data["performance"]
        perf["total_trades"] = perf.get("total_trades", 0) + 1

        if pnl > 0:
            perf["winning_trades"] = perf.get("winning_trades", 0) + 1
        else:
            perf["losing_trades"] = perf.get("losing_trades", 0) + 1

        total = perf.get("total_trades", 1)
        perf["win_rate"] = (perf.get("winning_trades", 0) / total) * 100

        perf["monthly_pnl"] = perf.get("monthly_pnl", 0) + pnl

        self.save_data(data)
        print(f"✓ Logged trade: {symbol} {trade_type} PnL: ${pnl:.0f}")

    def update_performance(
        self,
        sharpe_ratio: float = None,
        max_drawdown: float = None,
        monthly_return: float = None,
    ):
        """Update performance metrics."""
        data = self.load_data()
        perf = data["performance"]

        if sharpe_ratio is not None:
            perf["sharpe_ratio"] = sharpe_ratio

        if max_drawdown is not None:
            perf["max_drawdown"] = max_drawdown

        if monthly_return is not None:
            perf["monthly_return"] = monthly_return

        self.save_data(data)

    # ==================== ALERTS ====================

    def log_alert(
        self,
        message: str,
        alert_type: str = "info",
        time: str = None,
    ):
        """
        Log alert message.

        Args:
            message: Alert message
            alert_type: "info", "warning", "error"
            time: ISO timestamp (default: now)
        """
        data = self.load_data()

        alert = {
            "message": message,
            "type": alert_type,
            "time": time or datetime.now().isoformat(),
        }

        data["alerts"].append(alert)

        # Keep only last 100 alerts
        data["alerts"] = data["alerts"][-100:]

        self.save_data(data)

    # ==================== STATUS ====================

    def update_status(self, status: str, next_scan: str = None):
        """
        Update system status.

        Args:
            status: "READY", "RUNNING", "PAUSED", "ERROR"
            next_scan: Next scan time (e.g., "09:00 AM")
        """
        data = self.load_data()
        data["system_status"] = status

        if next_scan:
            data["next_scan"] = next_scan

        self.save_data(data)

    # ==================== REPORTING ====================

    def get_summary(self) -> Dict:
        """Get summary of current trading state."""
        data = self.load_data()

        return {
            "status": data["system_status"],
            "last_scan": data["last_scan"],
            "markets_count": len(data["markets"]),
            "open_positions": len(data["positions"]),
            "total_capital": data["capital"]["total"],
            "allocated_capital": data["capital"]["allocated"],
            "reserve_capital": data["capital"]["reserve"],
            "total_trades": data["performance"]["total_trades"],
            "win_rate": data["performance"]["win_rate"],
            "monthly_return": data["performance"]["monthly_return"],
        }

    def print_summary(self):
        """Print trading summary to console."""
        summary = self.get_summary()

        print("\n" + "="*60)
        print("TRADING SYSTEM SUMMARY")
        print("="*60)
        print(f"Status:          {summary['status']}")
        print(f"Last Scan:       {summary['last_scan']}")
        print(f"Markets:         {summary['markets_count']}")
        print(f"Open Positions:  {summary['open_positions']}")
        print(f"Allocated:       ${summary['allocated_capital']:,.0f}")
        print(f"Reserve:         ${summary['reserve_capital']:,.0f}")
        print(f"Total Trades:    {summary['total_trades']}")
        print(f"Win Rate:        {summary['win_rate']:.1f}%")
        print(f"Monthly Return:  {summary['monthly_return']:+.2f}%")
        print("="*60 + "\n")


# Example usage
if __name__ == "__main__":
    logger = TradingDataLogger()

    # Log market scan
    logger.log_market_scan(
        [
            {"symbol": "BTC-USD", "regime": "STRONG_UPTREND", "quality": 0.98, "allocation": 0.40},
            {"symbol": "ETH-USD", "regime": "UPTREND", "quality": 0.85, "allocation": 0.30},
            {"symbol": "SPY", "regime": "UPTREND", "quality": 0.80, "allocation": 0.30},
        ]
    )

    # Log position opened
    logger.log_position_opened("BTC-USD", entry_price=50500, quantity=0.5, stop_loss=50000, take_profit=51500)

    # Log alert
    logger.log_alert("BTC-USD signal generated", alert_type="info")

    # Print summary
    logger.print_summary()
