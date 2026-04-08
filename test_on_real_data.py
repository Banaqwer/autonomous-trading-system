"""
Simple test: Run Hurst system on real market data
"""
import pandas as pd
import numpy as np
import yfinance as yf
from hurst_cyclic_trading import HurstCyclicAlgorithm

# Download 3 years of SPY data
print("Downloading SPY data...")
df = yf.download('SPY', period='3y', progress=False)[['Close']]
print("Data shape:", df.shape)
print("Date range: {} to {}".format(df.index[0].date(), df.index[-1].date()))

# Run algorithm
print("\nRunning Hurst algorithm on SPY...")
try:
    algo = HurstCyclicAlgorithm(df, use_fld=False)
    report = algo.run()

    print("\nRESULTS:")
    for key, val in sorted(report.items()):
        if key != 'error':
            print("  {}: {}".format(key, val))

except Exception as e:
    print("Error:", e)
    import traceback
    traceback.print_exc()
