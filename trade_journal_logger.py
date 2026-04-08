"""
TRADE JOURNAL LOGGER
====================

Logs every trade with full context:
- Entry/exit details
- Win/loss reason
- Market conditions
- Pattern logic
- News events
- Performance analysis

Purpose: Understand WHY trades win or lose, not just THAT they do.
"""

import json
import csv
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Trade:
    """Complete record of a single trade"""

    # Identifiers
    trade_id: str
    asset: str
    date: str
    time_entry: str

    # Entry details
    entry_price: float
    entry_reason: str  # e.g., "Hurst cycle turning point", "Mean reversion signal"
    entry_pattern: str  # e.g., "Daily cycle bottom", "Weekly reversal"

    # Exit details
    time_exit: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: str = "OPEN"  # "Target hit", "Stop loss", "Manual close", "Overnight gap"

    # Results
    duration_minutes: Optional[int] = None
    profit_loss: Optional[float] = None
    profit_loss_percent: Optional[float] = None
    win: Optional[bool] = None  # True/False (None if still open)

    # Context
    market_condition: str = "UNKNOWN"  # "Trending", "Ranging", "Volatile", "Choppy"
    volatility_level: str = "NORMAL"  # "Low", "Normal", "High", "Spike"
    news_event: Optional[str] = None  # e.g., "Fed announcement", "Earnings", "Geopolitical"

    # Analysis
    loss_cause: Optional[str] = None  # "News gap", "False signal", "Regime change", etc.
    win_reason: Optional[str] = None  # "Pattern perfect", "News positive", "Momentum", etc.

    # Risk management
    position_size: float = 0.02  # 2% risk
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    risk_reward_ratio: Optional[float] = None

    # Additional notes
    notes: str = ""


class TradeJournal:
    """Manages trade journal - logs and analyzes all trades"""

    def __init__(self, journal_file="trade_journal.csv", json_file="trade_journal.json"):
        self.journal_file = Path(journal_file)
        self.json_file = Path(json_file)
        self.trades = []
        self.load_existing()

    def load_existing(self):
        """Load existing trades from journal"""
        if self.json_file.exists():
            try:
                with open(self.json_file, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get('trades', [])
            except:
                self.trades = []

    def log_entry(self, trade: Trade):
        """Log a new trade entry"""
        self.trades.append(asdict(trade))
        self._save()
        print(f"[OK] Trade logged: {trade.asset} at {trade.time_entry}")

    def log_exit(self, trade_id: str, exit_price: float, exit_reason: str,
                 profit_loss: float, market_condition: str = "", news_event: str = None,
                 loss_cause: str = None, win_reason: str = None):
        """Log a trade exit"""

        for trade in self.trades:
            if trade['trade_id'] == trade_id:
                trade['time_exit'] = datetime.now().strftime("%H:%M:%S")
                trade['exit_price'] = exit_price
                trade['exit_reason'] = exit_reason
                trade['profit_loss'] = profit_loss
                trade['market_condition'] = market_condition
                trade['news_event'] = news_event
                trade['loss_cause'] = loss_cause
                trade['win_reason'] = win_reason

                # Calculate metrics
                if trade['entry_price'] and exit_price:
                    trade['profit_loss_percent'] = (
                        (exit_price - trade['entry_price']) / trade['entry_price'] * 100
                    )

                trade['win'] = profit_loss > 0

                # Calculate risk/reward
                if trade['stop_loss'] and trade['target']:
                    risk = abs(trade['entry_price'] - trade['stop_loss'])
                    reward = abs(trade['target'] - trade['entry_price'])
                    if risk > 0:
                        trade['risk_reward_ratio'] = reward / risk

                self._save()
                print(f"[OK] Trade closed: {trade['asset']} - "
                      f"{'WIN' if trade['win'] else 'LOSS'} ${profit_loss:.2f}")
                return True

        return False

    def _save(self):
        """Save journal to files (CSV and JSON)"""
        # Save as JSON
        with open(self.json_file, 'w') as f:
            json.dump({'trades': self.trades, 'timestamp': datetime.now().isoformat()}, f, indent=2)

        # Save as CSV
        if self.trades:
            with open(self.journal_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.trades[0].keys())
                writer.writeheader()
                writer.writerows(self.trades)

    def get_statistics(self):
        """Calculate trade statistics"""
        if not self.trades:
            return {}

        closed_trades = [t for t in self.trades if t['win'] is not None]
        if not closed_trades:
            return {}

        wins = [t for t in closed_trades if t['win']]
        losses = [t for t in closed_trades if not t['win']]

        stats = {
            'total_trades': len(closed_trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': len(wins) / len(closed_trades) * 100 if closed_trades else 0,
            'avg_win': sum([t['profit_loss'] for t in wins]) / len(wins) if wins else 0,
            'avg_loss': sum([t['profit_loss'] for t in losses]) / len(losses) if losses else 0,
            'total_profit': sum([t['profit_loss'] for t in closed_trades]),
            'profit_factor': (
                sum([t['profit_loss'] for t in wins]) / abs(sum([t['profit_loss'] for t in losses]))
                if losses else 0
            ),
            'avg_risk_reward': (
                sum([t['risk_reward_ratio'] for t in closed_trades if t['risk_reward_ratio']]) /
                len([t for t in closed_trades if t['risk_reward_ratio']])
                if [t for t in closed_trades if t['risk_reward_ratio']] else 0
            ),
        }

        return stats

    def analyze_losses(self):
        """Analyze what causes losses"""
        closed_trades = [t for t in self.trades if t['win'] is not None]
        losses = [t for t in closed_trades if not t['win']]

        if not losses:
            return {}

        causes = {}
        for loss in losses:
            cause = loss['loss_cause'] or 'Unknown'
            causes[cause] = causes.get(cause, 0) + 1

        return {
            'total_losses': len(losses),
            'loss_causes': causes,
            'pct_by_cause': {k: v/len(losses)*100 for k, v in causes.items()}
        }

    def print_summary(self):
        """Print trade journal summary"""
        stats = self.get_statistics()
        loss_analysis = self.analyze_losses()

        print("\n" + "="*80)
        print("TRADE JOURNAL SUMMARY")
        print("="*80)

        if not stats:
            print("No closed trades yet.")
            return

        print(f"\nOverall Performance:")
        print(f"  Total Trades: {stats['total_trades']}")
        print(f"  Wins: {stats['wins']} | Losses: {stats['losses']}")
        print(f"  Win Rate: {stats['win_rate']:.1f}%")
        print(f"  Total Profit: ${stats['total_profit']:.2f}")
        print(f"  Avg Win: ${stats['avg_win']:.2f}")
        print(f"  Avg Loss: ${stats['avg_loss']:.2f}")
        print(f"  Profit Factor: {stats['profit_factor']:.2f}")
        print(f"  Avg Risk/Reward: {stats['avg_risk_reward']:.2f}")

        if loss_analysis['total_losses'] > 0:
            print(f"\nLoss Analysis ({loss_analysis['total_losses']} losses):")
            for cause, count in loss_analysis['loss_causes'].items():
                pct = loss_analysis['pct_by_cause'][cause]
                print(f"  {cause}: {count} trades ({pct:.1f}%)")

        print("\n" + "="*80)

    def export_for_review(self, filename="trade_review.html"):
        """Export trades as HTML for review"""
        closed_trades = [t for t in self.trades if t['win'] is not None]

        html = """
        <html>
        <head>
            <title>Trade Journal Review</title>
            <style>
                body { font-family: Arial; margin: 20px; background: #f5f5f5; }
                table { width: 100%; border-collapse: collapse; background: white; }
                th { background: #333; color: white; padding: 10px; text-align: left; }
                td { padding: 8px; border-bottom: 1px solid #ddd; }
                .win { background: #e8f5e9; }
                .loss { background: #ffebee; }
                .header { background: #1976d2; color: white; padding: 20px; margin-bottom: 20px; }
                h2 { color: #333; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Trade Journal Review</h1>
                <p>Generated: {}</p>
            </div>
        """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Stats section
        stats = self.get_statistics()
        if stats:
            html += f"""
            <h2>Summary Statistics</h2>
            <table>
                <tr>
                    <th>Metric</th>
                    <th>Value</th>
                </tr>
                <tr>
                    <td>Total Trades</td>
                    <td>{stats['total_trades']}</td>
                </tr>
                <tr>
                    <td>Win Rate</td>
                    <td>{stats['win_rate']:.1f}%</td>
                </tr>
                <tr>
                    <td>Total Profit</td>
                    <td>${stats['total_profit']:.2f}</td>
                </tr>
                <tr>
                    <td>Profit Factor</td>
                    <td>{stats['profit_factor']:.2f}</td>
                </tr>
            </table>
            """

        # Trades table
        html += """
        <h2>All Trades</h2>
        <table>
            <tr>
                <th>Date</th>
                <th>Asset</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P&L</th>
                <th>Reason</th>
                <th>Pattern</th>
                <th>Loss/Win Cause</th>
                <th>News</th>
            </tr>
        """

        for trade in closed_trades:
            row_class = "win" if trade['win'] else "loss"
            cause = trade['loss_cause'] if trade['loss_cause'] else trade['win_reason'] or ""
            html += f"""
            <tr class="{row_class}">
                <td>{trade['date']}</td>
                <td>{trade['asset']}</td>
                <td>${trade['entry_price']:.2f}</td>
                <td>${trade['exit_price']:.2f}</td>
                <td>${trade['profit_loss']:.2f}</td>
                <td>{trade['exit_reason']}</td>
                <td>{trade['entry_pattern']}</td>
                <td>{cause}</td>
                <td>{trade['news_event'] or '-'}</td>
            </tr>
            """

        html += """
        </table>
        </body>
        </html>
        """

        with open(filename, 'w') as f:
            f.write(html)

        print(f"[OK] Trade review exported to {filename}")


# Example usage
if __name__ == '__main__':
    journal = TradeJournal()

    # Log a sample entry
    entry_trade = Trade(
        trade_id="TRADE_20260408_001",
        asset="SPY",
        date="2026-04-08",
        time_entry="09:45:00",
        entry_price=425.50,
        entry_reason="Hurst cycle turning point",
        entry_pattern="Daily cycle bottom",
        stop_loss=423.00,
        target=428.00,
    )

    journal.log_entry(entry_trade)

    # Simulate exit
    journal.log_exit(
        "TRADE_20260408_001",
        exit_price=428.25,
        exit_reason="Target hit",
        profit_loss=2.75,
        market_condition="Ranging",
        win_reason="Pattern perfect - cycle turned as predicted"
    )

    journal.print_summary()
    journal.export_for_review()
