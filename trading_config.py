"""
Trading Configuration
All sensitive settings go here, kept separate from code
"""

# ==================== BROKERS ====================

ALPACA_CONFIG = {
    "API_KEY": "PK4ZYODVFWATIKV7U3PZMA6QRX",
    "SECRET_KEY": "HFjCEf6FhBkseGruACqLKk4tW2Yc2SuQeurrhC27dqaZ",
    "BASE_URL": "https://paper-api.alpaca.markets",  # Paper trading (simulated)
    # Uncomment line below for LIVE trading (real money):
    # "BASE_URL": "https://api.alpaca.markets",
}

KRAKEN_CONFIG = {
    "API_KEY": "2NSDSBiDCA1vW+SbSVbzFM1JcS93WdWU2dI29iHHoPvR+Vn7HiQUejc3",
    "PRIVATE_KEY": "sZUEFmBmdBeC1aOgpRZuUsHVyAjF3Ju47g+GH+hmB//tD0tG7PWVMBa6xB6A7s8B8qgP1W83l69mt8ven52qkfPU",
}

# ==================== TRADING PARAMETERS ====================

INITIAL_CAPITAL = 100000  # Starting capital ($100k for testing)

MARKETS_TO_TRADE = [
    ("BTC-USD", "daily"),      # Bitcoin - main crypto
    ("ETH-USD", "daily"),      # Ethereum - alternative crypto
    ("SPY", "daily"),          # S&P 500 - broad market stocks
    ("QQQ", "daily"),          # Nasdaq 100 - tech stocks
    ("EUR/USD", "daily"),      # Euro/Dollar - forex
    ("GLD", "daily"),          # Gold - safe commodity
]

# Position sizing
RISK_PER_TRADE = 0.02        # 2% risk per trade
MIN_QUALITY_THRESHOLD = 0.50  # Only trade if quality score > 0.5

# Allocation strategy
# Options: "single_best", "proportional", "top_n", "dynamic"
ALLOCATION_STRATEGY = "proportional"

# When to rebalance market rankings
REBALANCE_FREQUENCY_HOURS = 4  # Re-scan every 4 hours

# ==================== ALERTS ====================

# Email alerts
SEND_EMAIL_ALERTS = True
EMAIL_ADDRESS = "hhoenn@syr.edu"
EMAIL_PASSWORD = "Banaqwer007!"  # Gmail app password (not regular password)
EMAIL_RECIPIENT = "hhoenn@syr.edu"

# Telegram alerts (optional - set to False if not using)
SEND_TELEGRAM_ALERTS = False
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

# ==================== LOGGING ====================

LOG_DIRECTORY = "./trading_logs"
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR

VERBOSE = True  # Print to console

# ==================== LIMITS & STOPS ====================

MAX_DAILY_LOSS_PERCENT = 5.0    # Stop trading if down 5% today
MAX_POSITION_SIZE = 0.5         # Max 50% of capital in one trade
MIN_EQUITY_TO_TRADE = 50000     # Stop if equity drops below $50k
