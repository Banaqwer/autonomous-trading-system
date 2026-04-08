"""
TRADE ANALYSIS TOOL
===================

Analyzes trade journal to answer:
1. What patterns work best?
2. What causes losses?
3. Which assets perform best?
4. Which market conditions are best?
5. Does news really cause losses?
6. What's the optimal strategy?
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict


class TradeAnalyzer:
    """Analyzes trades to find patterns and answers"""

    def __init__(self, journal_file="trade_journal.json"):
        self.journal_file = Path(journal_file)
        self.trades = []
        self.load_trades()

    def load_trades(self):
        """Load trades from journal"""
        if self.journal_file.exists():
            with open(self.journal_file, 'r') as f:
                data = json.load(f)
                self.trades = data.get('trades', [])

    def get_closed_trades(self):
        """Get only closed trades"""
        return [t for t in self.trades if t['win'] is not None]

    def analyze_by_pattern(self):
        """Which patterns work best?"""
        closed = self.get_closed_trades()
        by_pattern = defaultdict(list)

        for trade in closed:
            pattern = trade['entry_pattern']
            by_pattern[pattern].append(trade)

        results = {}
        for pattern, trades in by_pattern.items():
            wins = [t for t in trades if t['win']]
            results[pattern] = {
                'total': len(trades),
                'wins': len(wins),
                'win_rate': len(wins) / len(trades) * 100,
                'avg_profit': sum([t['profit_loss'] for t in trades]) / len(trades),
                'total_profit': sum([t['profit_loss'] for t in trades]),
            }

        return results

    def analyze_by_asset(self):
        """Which assets perform best?"""
        closed = self.get_closed_trades()
        by_asset = defaultdict(list)

        for trade in closed:
            asset = trade['asset']
            by_asset[asset].append(trade)

        results = {}
        for asset, trades in by_asset.items():
            wins = [t for t in trades if t['win']]
            results[asset] = {
                'total': len(trades),
                'wins': len(wins),
                'win_rate': len(wins) / len(trades) * 100 if trades else 0,
                'avg_profit': sum([t['profit_loss'] for t in trades]) / len(trades) if trades else 0,
                'total_profit': sum([t['profit_loss'] for t in trades]),
            }

        return results

    def analyze_by_market_condition(self):
        """Does performance vary by market condition?"""
        closed = self.get_closed_trades()
        by_condition = defaultdict(list)

        for trade in closed:
            condition = trade['market_condition']
            by_condition[condition].append(trade)

        results = {}
        for condition, trades in by_condition.items():
            wins = [t for t in trades if t['win']]
            results[condition] = {
                'total': len(trades),
                'wins': len(wins),
                'win_rate': len(wins) / len(trades) * 100 if trades else 0,
                'avg_profit': sum([t['profit_loss'] for t in trades]) / len(trades) if trades else 0,
                'total_profit': sum([t['profit_loss'] for t in trades]),
            }

        return results

    def analyze_news_impact(self):
        """Does news impact win rate?"""
        closed = self.get_closed_trades()

        with_news = [t for t in closed if t['news_event']]
        without_news = [t for t in closed if not t['news_event']]

        def calc_stats(trades):
            if not trades:
                return {'total': 0, 'wins': 0, 'win_rate': 0, 'avg_profit': 0}
            wins = [t for t in trades if t['win']]
            return {
                'total': len(trades),
                'wins': len(wins),
                'win_rate': len(wins) / len(trades) * 100,
                'avg_profit': sum([t['profit_loss'] for t in trades]) / len(trades),
                'total_profit': sum([t['profit_loss'] for t in trades]),
            }

        return {
            'with_news': calc_stats(with_news),
            'without_news': calc_stats(without_news),
            'news_impact': (
                calc_stats(with_news)['win_rate'] - calc_stats(without_news)['win_rate']
            ) if without_news else 0
        }

    def analyze_loss_causes(self):
        """What causes losses?"""
        closed = self.get_closed_trades()
        losses = [t for t in closed if not t['win']]

        causes = defaultdict(lambda: {'count': 0, 'total_loss': 0})

        for loss in losses:
            cause = loss['loss_cause'] or 'Unknown'
            causes[cause]['count'] += 1
            causes[cause]['total_loss'] += loss['profit_loss']

        results = {}
        total_losses = len(losses)
        for cause, data in causes.items():
            results[cause] = {
                'count': data['count'],
                'percentage': data['count'] / total_losses * 100 if total_losses else 0,
                'avg_loss': data['total_loss'] / data['count'],
            }

        return results

    def analyze_win_reasons(self):
        """What causes wins?"""
        closed = self.get_closed_trades()
        wins = [t for t in closed if t['win']]

        reasons = defaultdict(lambda: {'count': 0, 'total_profit': 0})

        for win in wins:
            reason = win['win_reason'] or 'Unknown'
            reasons[reason]['count'] += 1
            reasons[reason]['total_profit'] += win['profit_loss']

        results = {}
        total_wins = len(wins)
        for reason, data in reasons.items():
            results[reason] = {
                'count': data['count'],
                'percentage': data['count'] / total_wins * 100 if total_wins else 0,
                'avg_profit': data['total_profit'] / data['count'],
            }

        return results

    def get_best_performers(self):
        """Rank what works best"""
        patterns = self.analyze_by_pattern()
        assets = self.analyze_by_asset()

        best_pattern = max(patterns.items(), key=lambda x: x[1]['win_rate']) if patterns else None
        best_asset = max(assets.items(), key=lambda x: x[1]['win_rate']) if assets else None
        most_profitable_pattern = max(patterns.items(), key=lambda x: x[1]['total_profit']) if patterns else None
        most_profitable_asset = max(assets.items(), key=lambda x: x[1]['total_profit']) if assets else None

        return {
            'best_win_rate_pattern': best_pattern,
            'best_win_rate_asset': best_asset,
            'most_profitable_pattern': most_profitable_pattern,
            'most_profitable_asset': most_profitable_asset,
        }

    def print_analysis(self):
        """Print full analysis"""
        if not self.get_closed_trades():
            print("No closed trades to analyze yet.")
            return

        print("\n" + "="*100)
        print("TRADE ANALYSIS REPORT")
        print("="*100)

        # By pattern
        print("\n1. PERFORMANCE BY PATTERN:")
        print("-" * 100)
        patterns = self.analyze_by_pattern()
        for pattern, stats in sorted(patterns.items(), key=lambda x: x[1]['win_rate'], reverse=True):
            print(f"  {pattern}:")
            print(f"    Trades: {stats['total']} | Win Rate: {stats['win_rate']:.1f}% | " +
                  f"Avg Profit: ${stats['avg_profit']:.2f} | Total: ${stats['total_profit']:.2f}")

        # By asset
        print("\n2. PERFORMANCE BY ASSET:")
        print("-" * 100)
        assets = self.analyze_by_asset()
        for asset, stats in sorted(assets.items(), key=lambda x: x[1]['win_rate'], reverse=True):
            print(f"  {asset}:")
            print(f"    Trades: {stats['total']} | Win Rate: {stats['win_rate']:.1f}% | " +
                  f"Avg Profit: ${stats['avg_profit']:.2f} | Total: ${stats['total_profit']:.2f}")

        # By market condition
        print("\n3. PERFORMANCE BY MARKET CONDITION:")
        print("-" * 100)
        conditions = self.analyze_by_market_condition()
        for condition, stats in sorted(conditions.items(), key=lambda x: x[1]['win_rate'], reverse=True):
            print(f"  {condition}:")
            print(f"    Trades: {stats['total']} | Win Rate: {stats['win_rate']:.1f}% | " +
                  f"Avg Profit: ${stats['avg_profit']:.2f}")

        # News impact
        print("\n4. NEWS IMPACT ANALYSIS:")
        print("-" * 100)
        news = self.analyze_news_impact()
        print(f"  Trades WITH news events: {news['with_news']['total']}")
        print(f"    Win Rate: {news['with_news']['win_rate']:.1f}%")
        print(f"    Avg Profit: ${news['with_news']['avg_profit']:.2f}")
        print(f"\n  Trades WITHOUT news: {news['without_news']['total']}")
        print(f"    Win Rate: {news['without_news']['win_rate']:.1f}%")
        print(f"    Avg Profit: ${news['without_news']['avg_profit']:.2f}")
        print(f"\n  IMPACT: {news['news_impact']:.1f}% win rate difference")
        if news['news_impact'] < 0:
            print(f"  ➜ NEWS HURTS: Loses ~{abs(news['news_impact']):.1f}% win rate")
        elif news['news_impact'] > 0:
            print(f"  ➜ NEWS HELPS: Gains ~{news['news_impact']:.1f}% win rate")
        else:
            print(f"  ➜ NO IMPACT: News doesn't affect win rate")

        # Loss causes
        print("\n5. WHAT CAUSES LOSSES:")
        print("-" * 100)
        losses = self.analyze_loss_causes()
        for cause, stats in sorted(losses.items(), key=lambda x: x[1]['count'], reverse=True):
            print(f"  {cause}: {stats['count']} losses ({stats['percentage']:.1f}%) | " +
                  f"Avg Loss: ${stats['avg_loss']:.2f}")

        # Win reasons
        print("\n6. WHAT CAUSES WINS:")
        print("-" * 100)
        wins = self.analyze_win_reasons()
        for reason, stats in sorted(wins.items(), key=lambda x: x[1]['count'], reverse=True):
            print(f"  {reason}: {stats['count']} wins ({stats['percentage']:.1f}%) | " +
                  f"Avg Win: ${stats['avg_profit']:.2f}")

        # Best performers
        print("\n7. TOP PERFORMERS:")
        print("-" * 100)
        best = self.get_best_performers()
        if best['best_win_rate_pattern']:
            print(f"  Best Pattern (Win Rate): {best['best_win_rate_pattern'][0]} " +
                  f"({best['best_win_rate_pattern'][1]['win_rate']:.1f}%)")
        if best['most_profitable_pattern']:
            print(f"  Most Profitable Pattern: {best['most_profitable_pattern'][0]} " +
                  f"(${best['most_profitable_pattern'][1]['total_profit']:.2f})")
        if best['best_win_rate_asset']:
            print(f"  Best Asset (Win Rate): {best['best_win_rate_asset'][0]} " +
                  f"({best['best_win_rate_asset'][1]['win_rate']:.1f}%)")
        if best['most_profitable_asset']:
            print(f"  Most Profitable Asset: {best['most_profitable_asset'][0]} " +
                  f"(${best['most_profitable_asset'][1]['total_profit']:.2f})")

        print("\n" + "="*100)


if __name__ == '__main__':
    analyzer = TradeAnalyzer()
    analyzer.print_analysis()
