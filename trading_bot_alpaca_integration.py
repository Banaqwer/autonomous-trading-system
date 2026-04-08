"""
HURST CYCLIC TRADING BOT - ALPACA INTEGRATION
============================================================================

Automated trading system that:
1. Runs Hurst algorithm on all 40 assets
2. Generates BUY/SELL signals
3. Executes trades through Alpaca API
4. Manages positions and risk
5. Logs all activity for analysis

PAPER TRADING MODE: Safe testing for 2 months
REAL TRADING MODE: Switch to live capital after validation

Start: Monday, April 8, 2026 at 9:30 AM EST
"""

import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
import warnings
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import sys
from io import StringIO

# Alpaca API
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm


class HurstTradingBot:
    """Automated Hurst trading bot with Alpaca integration"""

    def __init__(self, api_key, secret_key, paper_trading=True):
        """Initialize bot with Alpaca credentials"""

        self.api_key = api_key
        self.secret_key = secret_key
        self.paper_trading = paper_trading

        # Initialize Alpaca client
        self.client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper_trading)

        # Portfolio configuration (40 assets)
        self.portfolio = {
            # Original 15
            'USO': {'name': 'Oil', 'tier': 'core'},
            'TLT': {'name': 'Long Bonds', 'tier': 'core'},
            'MUB': {'name': 'Muni Bonds', 'tier': 'core'},
            'FXC': {'name': 'Canadian Dollar', 'tier': 'core'},
            'EWG': {'name': 'Germany', 'tier': 'core'},
            'IJH': {'name': 'Mid Cap', 'tier': 'core'},
            'VNQ': {'name': 'Real Estate', 'tier': 'core'},
            'DBC': {'name': 'Commodities', 'tier': 'core'},
            'GSG': {'name': 'Commodity ETF', 'tier': 'core'},
            'XLV': {'name': 'Healthcare', 'tier': 'core'},
            'VXX': {'name': 'VIX ETN', 'tier': 'core'},
            'QQQ': {'name': 'Nasdaq', 'tier': 'core'},
            'EWC': {'name': 'Canada ETF', 'tier': 'core'},
            'WEAT': {'name': 'Wheat', 'tier': 'core'},
            'FXE': {'name': 'Euro', 'tier': 'core'},

            # Top 25 new validated
            'ARKF': {'name': 'Ark Finance', 'tier': 'premium'},
            'EMQQ': {'name': 'Emerging Market Tech', 'tier': 'premium'},
            'VXUS': {'name': 'Total International', 'tier': 'premium'},
            'EWA': {'name': 'Australia', 'tier': 'premium'},
            'XLRE': {'name': 'Real Estate Alt', 'tier': 'premium'},
            'FXY': {'name': 'Japanese Yen', 'tier': 'premium'},
            'XLY': {'name': 'Consumer Discretionary', 'tier': 'premium'},
            'XLI': {'name': 'Industrials', 'tier': 'premium'},
            'SVXY': {'name': 'VIX Inverse', 'tier': 'premium'},
            'IEF': {'name': 'Treasury 7-10yr', 'tier': 'premium'},
            'UNG': {'name': 'Natural Gas', 'tier': 'standard'},
            'EWU': {'name': 'UK', 'tier': 'standard'},
            'EWJ': {'name': 'Japan', 'tier': 'standard'},
            'XLP': {'name': 'Consumer Staples', 'tier': 'standard'},
            'GLD': {'name': 'Gold', 'tier': 'standard'},
            'HYG': {'name': 'High Yield Bond', 'tier': 'standard'},
            'SCHP': {'name': 'TIPS', 'tier': 'standard'},
            'FXG': {'name': 'British Pound', 'tier': 'standard'},
            'IWM': {'name': 'Russell 2000', 'tier': 'standard'},
            'AGG': {'name': 'Aggregate Bond', 'tier': 'diversifier'},
            'BND': {'name': 'Broad Bond', 'tier': 'diversifier'},
            'GDX': {'name': 'Gold Miners', 'tier': 'diversifier'},
            'IEMG': {'name': 'Emerging Markets', 'tier': 'diversifier'},
            'UCO': {'name': 'Oil 2x', 'tier': 'diversifier'},
            'VTV': {'name': 'Value', 'tier': 'diversifier'},
        }

        # Risk management
        self.risk_per_trade = 0.02  # 2% of capital
        self.max_position_size = 0.10  # Max 10% per position
        self.max_daily_loss = 0.05  # Max 5% daily loss

        # Logging
        self.setup_logging()

        # Data storage
        self.data = {}
        self.signals = []
        self.trades = []
        self.performance = defaultdict(float)

    def setup_logging(self):
        """Configure logging"""
        log_dir = "trading_logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        log_file = os.path.join(log_dir, f"trading_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )

        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Trading bot initialized. Mode: {'PAPER' if self.paper_trading else 'REAL'}")

    def verify_alpaca_connection(self):
        """Verify connection to Alpaca"""
        try:
            account = self.client.get_account()
            self.logger.info(f"Connected to Alpaca. Account: {account.account_number}")
            self.logger.info(f"Cash available: ${account.cash}")
            self.logger.info(f"Equity: ${account.equity}")
            self.logger.info(f"Mode: {'PAPER TRADING' if self.paper_trading else 'LIVE TRADING'}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Alpaca: {str(e)}")
            return False

    def download_data(self, lookback_days=730):
        """Download historical data for all assets"""
        self.logger.info(f"Downloading {lookback_days}-day data for {len(self.portfolio)} assets...")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)

        for symbol in sorted(self.portfolio.keys()):
            try:
                data = yf.download(symbol, start=start_date, end=end_date, progress=False)
                if data is not None and len(data) > 300:
                    self.data[symbol] = data
                    self.logger.info(f"[OK] {symbol}: {len(data)} bars")
                else:
                    self.logger.warning(f"[SKIP] {symbol}: Insufficient data")
            except Exception as e:
                self.logger.error(f"[ERROR] {symbol}: {str(e)}")

        self.logger.info(f"Ready to trade: {len(self.data)}/{len(self.portfolio)} assets")

    def generate_signals(self):
        """Generate trading signals for all assets"""
        self.logger.info("Generating signals for all assets...")

        signals_today = []

        for symbol in sorted(self.portfolio.keys()):
            if symbol not in self.data:
                continue

            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                algo = HurstCyclicAlgorithm(self.data[symbol], use_fld=True)
                algo.run()

                sys.stdout = old_stdout

                if algo.report and 'error' not in algo.report:
                    signal = algo.report.get('signal', None)
                    confidence = algo.report.get('confluence_score', 0)

                    if signal in ['BUY', 'SELL'] and confidence > 0.20:
                        signals_today.append({
                            'symbol': symbol,
                            'side': signal,
                            'confidence': confidence,
                            'tier': self.portfolio[symbol]['tier'],
                        })

            except Exception as e:
                self.logger.error(f"Signal generation failed for {symbol}: {str(e)}")

        if signals_today:
            self.logger.info(f"Generated {len(signals_today)} signals")
            for sig in signals_today:
                self.logger.info(f"  {sig['symbol']}: {sig['side']} (confidence: {sig['confidence']:.2f})")

        self.signals.extend(signals_today)
        return signals_today

    def calculate_position_size(self, symbol, side, confidence):
        """Calculate position size based on confidence and risk"""
        account = self.client.get_account()
        cash = float(account.cash)

        # Risk per trade
        risk_amount = cash * self.risk_per_trade

        # Get current price
        current_price = self.data[symbol]['Close'].iloc[-1]

        # Simple position size: risk_amount / current_price
        position_size = int(risk_amount / current_price)

        # Enforce max position size
        max_position = int((float(account.equity) * self.max_position_size) / current_price)
        position_size = min(position_size, max_position)

        return max(position_size, 1)  # At least 1 share

    def execute_trade(self, symbol, side, confidence):
        """Execute a single trade"""
        try:
            position_size = self.calculate_position_size(symbol, side, confidence)

            # Create market order
            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=position_size,
                side=OrderSide.BUY if side == 'BUY' else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )

            # Submit order
            order = self.client.submit_order(order_request)

            # Log trade
            trade_log = {
                'timestamp': datetime.now(),
                'symbol': symbol,
                'side': side,
                'quantity': position_size,
                'confidence': confidence,
                'order_id': order.id,
                'status': order.status,
            }

            self.trades.append(trade_log)

            self.logger.info(f"EXECUTED: {symbol} {side} {position_size} shares (confidence: {confidence:.2f})")

            return True

        except Exception as e:
            self.logger.error(f"Trade execution failed for {symbol}: {str(e)}")
            return False

    def process_signals(self, signals):
        """Process generated signals and execute trades"""
        if not signals:
            self.logger.info("No signals to process")
            return

        self.logger.info(f"Processing {len(signals)} signals...")

        for sig in signals:
            # Execute if confidence above threshold
            if sig['confidence'] > 0.25:
                self.execute_trade(sig['symbol'], sig['side'], sig['confidence'])

    def update_account_status(self):
        """Log current account status"""
        try:
            account = self.client.get_account()
            self.logger.info(f"Account Status: Cash=${account.cash}, Equity=${account.equity}, PnL=${float(account.equity) - 100000:,.0f}")
        except Exception as e:
            self.logger.error(f"Failed to get account status: {str(e)}")

    def run_daily(self):
        """Execute daily trading routine"""
        self.logger.info("="*100)
        self.logger.info(f"DAILY RUN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("="*100)

        # Verify connection
        if not self.verify_alpaca_connection():
            self.logger.error("Cannot proceed without Alpaca connection")
            return False

        # Download fresh data
        self.download_data()

        # Generate signals
        signals = self.generate_signals()

        # Execute trades
        self.process_signals(signals)

        # Update status
        self.update_account_status()

        self.logger.info("="*100)
        return True

    def run_continuous(self, check_interval_minutes=5):
        """Run bot continuously, checking for signals every N minutes"""
        self.logger.info(f"Starting continuous trading mode (check interval: {check_interval_minutes} min)")

        while True:
            try:
                self.run_daily()
                self.logger.info(f"Next check in {check_interval_minutes} minutes...")
                import time
                time.sleep(check_interval_minutes * 60)
            except KeyboardInterrupt:
                self.logger.info("Bot stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error: {str(e)}")


def main():
    """Initialize and run trading bot"""

    # Get API credentials from environment or config
    api_key = os.getenv('ALPACA_API_KEY')
    secret_key = os.getenv('ALPACA_SECRET_KEY')

    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables not set")
        print("\nTo use this bot, set your Alpaca credentials:")
        print("  export ALPACA_API_KEY='your_key'")
        print("  export ALPACA_SECRET_KEY='your_secret'")
        return

    # Initialize bot
    bot = HurstTradingBot(api_key, secret_key, paper_trading=True)

    # Run daily test
    bot.run_daily()


if __name__ == '__main__':
    main()
