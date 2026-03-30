"""
Autonomous Trading System Scheduler
Runs the trading system automatically at configured times
"""

import schedule
import time
from datetime import datetime
import logging
from trading_config import *
from autonomous_multi_market_trader import AutonomousMultiMarketTrader, MarketAllocationStrategy
import yfinance as yf

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AutonomousTradingScheduler:
    """Manages automated trading cycles."""

    def __init__(self):
        """Initialize scheduler."""
        self.trader = AutonomousMultiMarketTrader(
            markets=MARKETS_TO_TRADE,
            initial_capital=INITIAL_CAPITAL,
            min_quality_threshold=MIN_QUALITY_THRESHOLD,
            allocation_strategy=self._get_strategy(),
            rebalance_frequency_minutes=REBALANCE_FREQUENCY_HOURS * 60,
            verbose=VERBOSE
        )
        logger.info(f"Trader initialized with ${INITIAL_CAPITAL:,} capital")

    def _get_strategy(self):
        """Convert strategy name to enum."""
        strategies = {
            "single_best": MarketAllocationStrategy.SINGLE_BEST,
            "proportional": MarketAllocationStrategy.PROPORTIONAL,
            "top_n": MarketAllocationStrategy.TOP_N,
            "dynamic": MarketAllocationStrategy.DYNAMIC,
        }
        return strategies.get(ALLOCATION_STRATEGY, MarketAllocationStrategy.PROPORTIONAL)

    def fetch_market_data(self):
        """Fetch latest price data for all configured markets."""
        logger.info("Fetching market data...")
        market_data = {}

        fetch_map = {
    "BTC-USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "EUR/USD": "EURUSD=X",
    "GLD": "GLD",
}

        try:
            for market_symbol, yf_symbol in fetch_map.items():
                matching = [m for m in MARKETS_TO_TRADE if m[0] == market_symbol]
                if not matching:
                    continue

                for symbol, timeframe in matching:
                    logger.info(f"  Downloading {symbol} ({timeframe})...")
                    df = yf.download(yf_symbol, period="1y", interval="1d", progress=False)

                    if df.empty:
                        logger.warning(f"  No data for {symbol}, skipping")
                        continue

                    if symbol not in market_data:
                        market_data[symbol] = {}

                    market_data[symbol][timeframe] = df["Close"].values.flatten()

            logger.info(f"✓ Fetched data for {len(market_data)} markets")
            return market_data

        except Exception as e:
            logger.error(f"Error fetching market data: {e}")
            return {}

    def run_trading_cycle(self):
        """Execute one complete trading cycle."""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"\n{'='*80}")
            logger.info(f"TRADING CYCLE: {timestamp}")
            logger.info(f"{'='*80}")

            market_data = self.fetch_market_data()

            if not market_data:
                logger.error("No market data available, skipping cycle")
                return

            self.trader.run_trading_cycle(market_data)
            logger.info("✓ Trading cycle complete")

        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)

    def schedule_jobs(self):
        """Schedule trading cycles."""
        schedule.every().day.at("09:00").do(self.run_trading_cycle)
        logger.info("✓ Scheduled daily trading cycle at 09:00 AM")

    def run(self):
        """Run scheduler indefinitely."""
        logger.info(f"\n{'='*80}")
        logger.info("AUTONOMOUS TRADING SYSTEM STARTED")
        logger.info(f"{'='*80}\n")

        self.schedule_jobs()

        while True:
            schedule.run_pending()
            time.sleep(60)


if __name__ == "__main__":
    scheduler = AutonomousTradingScheduler()
    scheduler.run()
