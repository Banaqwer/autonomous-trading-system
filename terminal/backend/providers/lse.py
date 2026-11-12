"""London Strategic Edge (LSE Data 3.0) via the official `lse-data` SDK (owner: FB).

Needs LSE_API_KEY. IMPORTANT — LSE bills ROWS READ, not rows returned: one FX daily
candle call can read 500k rows of a 12.5M/day quota (terminal/audit/live-sources.json).

    preview(market, symbol, timeframe)   0 rows billed: the last ten bars. The only LSE
                                         price call the terminal makes (quote fallback).
    options(underlying)                  not row-billed.
    candles / series / economic_calendar ROW-BILLED. Always refused inside the gateway:
                                         there is no environment switch, so no route can
                                         be turned into a quota burner by configuration.
                                         (An offline script that really wants them sets
                                         `lse.ALLOW_BILLED = True` in Python.)

Every call counts against 60 calls/minute: cache at least 60 s per symbol. The client is
created with retries=0 and wait_for_limit=False so a throttled call fails fast instead
of sleeping inside a worker thread.
"""
from __future__ import annotations

import os

from core import status
from core.errors import ApiError, NotConfigured, RateLimited, UpstreamError
from core.util import date_epoch, iso_epoch, num

SOURCE = "lse"


def configured() -> bool:
    return bool(os.environ.get("LSE_API_KEY"))


def _client(**kw):
    try:
        from lse import LSE
    except ImportError as e:
        raise NotConfigured("python package 'lse-data' is not installed (pip install lse-data)",
                            source=SOURCE) from e
    if not configured():
        raise NotConfigured("LSE_API_KEY is not set — get a free key at londonstrategicedge.com/data",
                            source=SOURCE)
    return LSE(**kw)  # reads LSE_API_KEY from env


ALLOW_BILLED = False      # never set by the gateway, never read from the environment


def _billed(name: str) -> None:
    if ALLOW_BILLED is not True:
        raise NotConfigured(
            f"LSE {name}() is row-billed (it bills rows read, up to 500k per call) and is "
            "disabled in the terminal — no page-reachable route may call it", source=SOURCE)


def _call(fn):
    """Run one SDK call, record it on the status board, map SDK errors to gateway errors."""
    from core import http
    try:
        out = fn()
    except ApiError:
        raise
    except Exception as e:                       # LSEError and transport errors
        code = getattr(e, "status", None) or getattr(e, "status_code", None) or getattr(e, "code", None)
        msg = http.scrub(str(e))[:300] or type(e).__name__
        status.error(SOURCE, msg)
        if code == 429 or "[429]" in msg:
            raise RateLimited(SOURCE, msg) from None
        raise UpstreamError(SOURCE, code if isinstance(code, int) else None, msg) from None
    status.ok(SOURCE)
    return out


# ----------------------------------------------------------------- zero-row call
def preview(market: str, symbol: str, timeframe: str = "1m") -> dict:
    """The last ten bars of a selection — bills 0 rows. Raw SDK dict (`rows`: [{time,
    open, high, low, close, volume, trades[, date]}], time 'YYYY-MM-DD HH:MM:SS' UTC)."""
    def run():
        with _client(retries=0, wait_for_limit=False, timeout=15) as c:
            return c.preview(market, symbol, timeframe)
    return _call(run)


def parse_preview(payload: dict) -> list[dict]:
    """[{t, o, h, l, c, v, date?}…] ascending; rows without a close are skipped."""
    out = []
    for r in (payload or {}).get("rows") or []:
        t, c = iso_epoch(r.get("time")), num(r.get("close"))
        if t is None or c is None:
            continue
        row = {"t": t, "o": num(r.get("open")), "h": num(r.get("high")), "l": num(r.get("low")),
               "c": c, "v": num(r.get("volume"))}
        if r.get("date"):
            row["date"] = str(r["date"])[:10]
            row["date_t"] = date_epoch(row["date"])
        out.append(row)
    out.sort(key=lambda r: r["t"])
    return out


def quote_from_previews(minute: list[dict], daily: list[dict]) -> dict | None:
    """Last price + previous close from a 1m preview and a 1d preview (both parsed).

    last/ts = the newest 1m bar (or the newest daily close when there is no 1m bar).
    A daily bar starts at its `t` and covers 24 h: the session the last bar belongs to
    supplies open/high/low/volume, and the daily bar before it the previous close."""
    if not minute and not daily:
        return None
    if minute:
        last, ts = minute[-1]["c"], minute[-1]["t"]
    else:
        last, ts = daily[-1]["c"], daily[-1]["t"] + 86399
    session = prev = None
    started = [i for i, d in enumerate(daily) if d["t"] <= ts]
    if started:
        i = started[-1]
        if ts < daily[i]["t"] + 86400:              # inside that daily bar
            session, prev = daily[i], (daily[i - 1] if i > 0 else None)
        else:                                       # a newer session than the last daily bar
            prev = daily[i]
    return {"last": last, "ts": ts, "prev_close": prev["c"] if prev else None,
            "open": session["o"] if session else None, "high": session["h"] if session else None,
            "low": session["l"] if session else None, "volume": session["v"] if session else None}


# -------------------------------------------------------------- not row-billed
def options(underlying, type=None, max_dte=60):
    def run():
        with _client() as c:
            kw = {"max_dte": max_dte}
            if type:
                kw["type"] = type
            return c.options(underlying, **kw)          # rows carry iv, delta, gamma, theta, vega
    return _call(run)


def options_flow(underlying=None, min_premium=100_000, limit=200):
    def run():
        with _client() as c:
            return (c.options_flow(underlying, min_premium=min_premium)
                    if underlying else c.options_flow(min_premium=min_premium, limit=limit))
    return _call(run)


def markets():
    def run():
        with _client() as c:
            return c.markets()
    return _call(run)


def usage():
    """Plan + quota state (rows used today, calls/minute). Not row-billed."""
    def run():
        with _client(retries=0, wait_for_limit=False, timeout=15) as c:
            return c.usage()
    return _call(run)


# ------------------------------------------------------- ROW-BILLED (disabled)
def candles(symbol, timeframe="1d", start=None, end=None, limit=500, market=None):
    _billed("candles")
    def run():
        with _client() as c:
            kw = {"start": start, "end": end, "limit": limit}
            if market:
                kw["market"] = market
            return c.candles(symbol, timeframe, **kw)
    return _call(run)


def series(code, start=None):
    _billed("series")
    def run():
        with _client() as c:
            return c.series(code, start=start) if start else c.series(code)   # macro + bond yields
    return _call(run)


def economic_calendar(region="US", start=None):
    _billed("economic_calendar")
    def run():
        with _client() as c:
            return c.economic_calendar(region=region, start=start)
    return _call(run)


def stream(symbols, on_tick):
    """Live quotes: calls on_tick(tick) for each. Blocks; run in a thread/task."""
    with _client() as c:
        c.on("tick", on_tick)
        c.connect(symbols)
