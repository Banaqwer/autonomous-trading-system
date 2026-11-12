"""Alpaca Market Data (IEX feed, free plan) — US equity snapshots and bars (owner: FB).

Needs APCA_API_KEY_ID / APCA_API_SECRET_KEY (already used for the paper account).
200 requests/minute. IEX is ONE venue: prices can differ by cents from the
consolidated tape and intraday volume is IEX-only. Daily/weekly bars are requested
from the consolidated SIP feed with `end` 16 minutes in the past (allowed on the free
plan), so their volume is the real consolidated volume, 15 minutes delayed.
"""
from __future__ import annotations

import os
import time

from core import http
from core.errors import NotConfigured, UpstreamError
from core.util import date_epoch, iso_epoch, num, week_floor

BASE = "https://data.alpaca.markets"
SOURCE = "alpaca"
TIMEFRAME = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day", "1w": "1Week"}
LOOKBACK_DAYS = {"1m": 7, "5m": 30, "15m": 90, "1h": 365, "1d": 1500, "1w": 7300}
SIP_DELAY = 16 * 60


def configured() -> bool:
    return bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))


def _headers() -> dict:
    if not configured():
        raise NotConfigured("APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set", source=SOURCE)
    return {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}


# ------------------------------------------------------------------ fetch
def snapshots(symbols, feed: str = "iex", timeout: float = 8) -> dict:
    """feed="iex": real time, one venue. feed="delayed_sip": consolidated tape, 15 minutes
    delayed (official closes and consolidated volume) — both are on the free plan."""
    url = f"{BASE}/v2/stocks/snapshots?" + http.qs({"symbols": ",".join(symbols), "feed": feed})
    return http.get_json(url, _headers(), timeout, source=SOURCE)


MAX_PAGES = 4


def bars(symbol: str, tf: str, limit: int = 300, timeout: float = 10, now: float | None = None) -> dict:
    """{"bars": [...]} newest first (sort=desc) so `limit` keeps the latest bars. Alpaca may
    answer with fewer bars than `limit` plus a next_page_token: pages are followed
    (at most MAX_PAGES requests) until `limit` bars are collected."""
    now = time.time() if now is None else now
    daily = tf in ("1d", "1w")
    limit = max(1, min(int(limit), 10000))
    params = {
        "timeframe": TIMEFRAME[tf], "limit": limit,
        "adjustment": "split", "sort": "desc",
        "feed": "sip" if daily else "iex",
        "start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - LOOKBACK_DAYS[tf] * 86400)),
    }
    if daily:
        params["end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - SIP_DELAY))
    out, token = [], None
    for _ in range(MAX_PAGES):
        url = f"{BASE}/v2/stocks/{symbol}/bars?" + http.qs({**params, "page_token": token})
        page = http.get_json(url, _headers(), timeout, source=SOURCE)
        out.extend(page.get("bars") or [])
        token = page.get("next_page_token")
        if not token or len(out) >= limit:
            break
    return {"bars": out[:limit]}


# ------------------------------------------------------------------ parse
def parse_snapshots(payload: dict, session_open: bool, close_ts=None) -> dict:
    """{symbol: {last, prev_close, bid, ask, open, high, low, volume, ts, regular, day}}.

    While the regular session is open: last = latest trade, bid/ask = top of book.
    Otherwise: last = the regular-session close (dailyBar.c) — the latest trade may be an
    extended-hours print far from the close — and bid/ask are None (after-hours quotes
    are placeholders). `day` is the date of the daily bar ('YYYY-MM-DD'), `day_close` its close. `close_ts(date_iso)` maps the daily bar's date to the
    time of its close (None -> the latest trade time is used)."""
    out = {}
    if not isinstance(payload, dict):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (expected {symbol: snapshot})")
    for sym, s in payload.items():
        if not isinstance(s, dict):
            continue
        day, prev = s.get("dailyBar") or {}, s.get("prevDailyBar") or {}
        trade, quote = s.get("latestTrade") or {}, s.get("latestQuote") or {}
        trade_ts = iso_epoch(trade.get("t"))
        if session_open:
            last, ts = num(trade.get("p")), trade_ts
            bid, ask = num(quote.get("bp")) or None, num(quote.get("ap")) or None
        else:
            last = num(day.get("c"))
            ts = (close_ts(day.get("t")) if close_ts and day.get("t") else None)
            if ts is None or (trade_ts is not None and trade_ts < ts):
                ts = trade_ts if trade_ts is not None else iso_epoch(day.get("t"))
            bid = ask = None
        if last is None:
            last, ts = num(trade.get("p")), trade_ts
        if last is None:
            continue
        out[sym] = {"last": last, "prev_close": num(prev.get("c")), "bid": bid, "ask": ask,
                    "open": num(day.get("o")), "high": num(day.get("h")), "low": num(day.get("l")),
                    "volume": num(day.get("v")), "ts": ts, "regular": bool(session_open),
                    "day": str(day.get("t") or "")[:10] or None, "day_close": num(day.get("c"))}
    return out


def parse_bars(payload: dict, tf: str) -> list[dict]:
    """[{t,o,h,l,c,v}…] (any order). Daily `t` = 00:00 UTC of the bar date; weekly = its Monday."""
    rows = []
    for b in (payload or {}).get("bars") or []:
        if tf in ("1d", "1w"):
            t = date_epoch(b.get("t"))
            if t is not None and tf == "1w":
                t = week_floor(int(t))
        else:
            t = iso_epoch(b.get("t"))
        o, h, l, c = (num(b.get(k)) for k in "ohlc")
        if t is None or None in (o, h, l, c):
            continue
        rows.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": num(b.get("v")) or 0.0})
    return rows
