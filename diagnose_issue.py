"""
Diagnose the indexing issue
"""
import numpy as np
import pandas as pd
import yfinance as yf

# Download SPY
df = yf.download('SPY', period='3y', progress=False)[['Close']]
prices = df['Close'].values.astype(float)

print("Prices shape:", prices.shape)
print("Prices type:", type(prices))
print("Prices length:", len(prices))

# Do the same detrending
log_prices = np.log(prices)
x = np.arange(len(log_prices))
coeffs = np.polyfit(x, log_prices, 1)
trend = np.polyval(coeffs, x)
detrended = log_prices - trend

print("\nDetrended shape:", detrended.shape)
print("Detrended length:", len(detrended))

# Do FFT
n = len(detrended)
freqs = np.fft.rfftfreq(n, d=1.0)
fft_vals = np.fft.rfft(detrended)
power = np.abs(fft_vals) ** 2

print("\nFFT info:")
print("  n =", n)
print("  freqs shape:", freqs.shape)
print("  freqs length:", len(freqs))
print("  fft_vals shape:", fft_vals.shape)
print("  fft_vals length:", len(fft_vals))
print("  power shape:", power.shape)
print("  power length:", len(power))

# Try the mask
periods = np.zeros_like(freqs)
periods[1:] = 1.0 / freqs[1:]
periods[0] = np.inf

print("\nPeriods info:")
print("  periods shape:", periods.shape)
print("  periods length:", len(periods))

# Create a mask
low = 90
high = 110
mask = (periods >= low) & (periods <= high)
print("\nMask info:")
print("  mask shape:", mask.shape)
print("  mask length:", len(mask))

# Try to apply mask
local_power = power.copy()
print("\nLocal power info before masking:")
print("  local_power shape:", local_power.shape)
print("  local_power length:", len(local_power))

try:
    local_power[~mask] = 0
    print("Masking successful!")
except Exception as e:
    print("Masking failed:", e)
