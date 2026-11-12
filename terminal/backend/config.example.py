"""Copy this to config.py and fill in your keys (config.py is gitignored).
The code reads os.environ first, so you can also set these as environment
variables / cloud secrets instead of editing this file.

Keys the market-data foundation uses (everything else works without them):
  APCA_API_KEY_ID / APCA_API_SECRET_KEY   real-time US equity quotes + bars (Alpaca IEX);
                                          without them equities fall back to Yahoo (delayed)
  TIINGO_API_KEY                          fallback for equities and FX when Yahoo fails
  FRED_API_KEY                            fallback for US10Y / US2Y (end of day)
  LSE_API_KEY                             fallback for futures (zero-row preview only)
  AISSTREAM_API_KEY                       vessel positions (SHIP)
/api/status shows which keys are present (booleans only, never a value).

Not keys — runtime switches, set them in the environment when starting the gateway:
  OT_FEEDS=binance,deribit,ais,alpaca     upstream websocket feeds (unset = all configured;
                                          "none" = no feed). AISStream allows 3 connections
                                          per key, Alpaca 1 stream per account.
  OT_QUOTES=0                             switch the background quote poller off (tests)
Row-billed LSE calls (candles / series / economic_calendar) are always refused by the
gateway: there is no switch for them (they bill rows READ, up to 500k per call).
"""
import os
_DEFAULTS = {
    # core
    "LSE_API_KEY": "", "FRED_API_KEY": "", "AISSTREAM_API_KEY": "", "MARKETAUX_API_KEY": "",
    # execution (paper only)
    "APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": "", "APCA_API_BASE_URL": "https://paper-api.alpaca.markets",
    # causal sensors
    "EIA_API_KEY": "", "FIRMS_MAP_KEY": "", "OPENFDA_API_KEY": "", "CENSUS_API_KEY": "",
    "BLS_API_KEY": "", "LABORCURRENT_API_KEY": "", "DOL_API_KEY": "", "DATA_GOV_API_KEY": "",
    "NASS_API_KEY": "", "USDA_MARS_API_KEY": "", "SEC_USER_AGENT": "Your Name your@email",
    # data breadth
    "TIINGO_API_KEY": "", "ALPHAVANTAGE_API_KEY": "", "FMP_API_KEY": "", "API_NINJAS_KEY": "",
    # satellite / environmental
    "CDSE_CLIENT_ID": "", "CDSE_CLIENT_SECRET": "", "NCEI_TOKEN": "", "OPENAQ_API_KEY": "",
    "FMCSA_WEBKEY": "", "SOCRATA_APP_TOKEN": "",
    # optional reasoning / litigation
    "ANTHROPIC_API_KEY": "", "COURTLISTENER_API_TOKEN": "",
}
for _k, _v in _DEFAULTS.items():
    if _v:
        os.environ.setdefault(_k, _v)
