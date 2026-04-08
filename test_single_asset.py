"""Test single asset to verify scanner works"""
import pandas as pd
import yfinance as yf
import warnings
import sys
from io import StringIO

warnings.filterwarnings('ignore')

from hurst_cyclic_trading import HurstCyclicAlgorithm

print("Starting single asset test...")
print("Downloading IWM data...")

data = yf.download('IWM', start='2023-01-01', end='2024-12-31', progress=False)
print(f"Downloaded {len(data)} bars")

print("Running Hurst algorithm...")

old_stdout = sys.stdout
sys.stdout = StringIO()

algo = HurstCyclicAlgorithm(data, use_fld=True)
report = algo.run()

sys.stdout = old_stdout

print(f"Algorithm completed")
print(f"Report keys: {list(algo.report.keys())}")
print(f"Total trades: {algo.report.get('total_trades', 0)}")
print(f"Win rate: {algo.report.get('win_rate', 0):.2%}")
print(f"Total return: {algo.report.get('total_return_pct', 0):.2f}%")

# Calculate trades per year
days = (pd.Timestamp('2024-12-31') - pd.Timestamp('2023-01-01')).days
years = days / 365.25
trades_per_year = algo.report.get('total_trades', 0) / years
print(f"Trades per year: {trades_per_year:.1f}")

print("Test completed successfully!")
