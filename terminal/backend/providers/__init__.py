"""Open-data provider adapters for OPEN TERMINAL.

No-key:  deribit (crypto options/IV/greeks/GEX), binance (spot book/trades/klines),
         nhc (NOAA hurricanes), french (Fama-French factors), gibs (NASA tiles).
Key:     lse (London Strategic Edge, LSE_API_KEY), fred (FRED_API_KEY),
         ais (AISStream, AISSTREAM_API_KEY).
Every adapter returns plain dicts/lists so the FastAPI gateway can serialize them.
"""
