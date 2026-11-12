"""Tiingo — fallback quotes (owner: FB). Needs TIINGO_API_KEY.

  iex(tickers)     IEX equity quotes with prevClose (use tngoLast: `last` is null
                   outside trading hours)
  fx_top(tickers)  real-time FX top of book (bid / ask / mid; no previous close)

The free-tier allowance is small and unmeasured: only used when the primary sources
of an instrument failed, and never more often than once a minute (core.quotes).
"""
from __future__ import annotations

import os

from core import http
from core.errors import NotConfigured
from core.util import iso_epoch, num

BASE = "https://api.tiingo.com"
SOURCE = "tiingo"


def configured() -> bool:
    return bool(os.environ.get("TIINGO_API_KEY"))


def _headers() -> dict:
    if not configured():
        raise NotConfigured("TIINGO_API_KEY is not set", source=SOURCE)
    # header auth: the key never appears in a URL (and so never in an error message)
    return {"Authorization": f"Token {os.environ['TIINGO_API_KEY']}",
            "Content-Type": "application/json"}


def iex(tickers, timeout: float = 10):
    url = f"{BASE}/iex/?" + http.qs({"tickers": ",".join(t.lower() for t in tickers)})
    return http.get_json(url, _headers(), timeout, source=SOURCE)


def fx_top(tickers, timeout: float = 10):
    url = f"{BASE}/tiingo/fx/top?" + http.qs({"tickers": ",".join(t.lower() for t in tickers)})
    return http.get_json(url, _headers(), timeout, source=SOURCE)


def parse_iex(payload) -> dict:
    """{TICKER: {last, prev_close, bid, ask, open, high, low, volume, ts}}."""
    out = {}
    for row in payload if isinstance(payload, list) else []:
        last = num(row.get("tngoLast"))
        if last is None:
            last = num(row.get("last"))
        if last is None or not row.get("ticker"):
            continue
        out[str(row["ticker"]).upper()] = {
            "last": last, "prev_close": num(row.get("prevClose")),
            "bid": num(row.get("bidPrice")), "ask": num(row.get("askPrice")),
            "open": num(row.get("open")), "high": num(row.get("high")), "low": num(row.get("low")),
            "volume": num(row.get("volume")),
            "ts": iso_epoch(row.get("lastSaleTimestamp")) or iso_epoch(row.get("timestamp")),
        }
    return out


def parse_fx_top(payload) -> dict:
    """{TICKER: {last (mid), bid, ask, ts}} — there is no previous close in this feed."""
    out = {}
    for row in payload if isinstance(payload, list) else []:
        bid, ask = num(row.get("bidPrice")), num(row.get("askPrice"))
        mid = num(row.get("midPrice"))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2
        if mid is None or not row.get("ticker"):
            continue
        out[str(row["ticker"]).upper()] = {"last": mid, "bid": bid, "ask": ask,
                                           "ts": iso_epoch(row.get("quoteTimestamp"))}
    return out
