"""Yahoo Finance chart / spark API — keyless, unofficial (owner: FB).

Quotes for futures, FX, indices, US10Y (and equities as fallback) and OHLCV candles.
Tested recipes: terminal/audit/live-sources.json. One spark call returns price,
previous close, day range and a 5-minute sparkline for up to 20 symbols.

Caveats (measured 2026-11-12): futures are ~10 min delayed, CBOE indices (^VIX, ^TNX)
~15 min, FX within about a minute. No CORS: always call from the gateway, through
core.cache. Arrays contain nulls. The last point of a chart is a "live" point stamped
with the last trade time, not a bar boundary.
"""
from __future__ import annotations

import urllib.parse

import datetime as dt

from core import http, session
from core.errors import UpstreamError
from core.util import day_floor, num, week_floor

BASE = "https://query1.finance.yahoo.com"
SOURCE = "yahoo"
INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1w": "1wk"}
STEP = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1w": 604800}
MAX_LOOKBACK_DAYS = {"1m": 7, "5m": 59, "15m": 59, "1h": 729}     # Yahoo's intraday limits
SPARK_MAX = 20                                                      # symbols per spark call


# ------------------------------------------------------------------ fetch
def spark(symbols, rng: str = "1d", interval: str = "5m", timeout: float = 10) -> dict:
    """Raw spark payload for up to 20 symbols (one HTTP call)."""
    symbols = list(symbols)
    if not symbols or len(symbols) > SPARK_MAX:
        raise ValueError(f"spark takes 1..{SPARK_MAX} symbols")
    url = f"{BASE}/v7/finance/spark?" + http.qs(
        {"symbols": ",".join(symbols), "range": rng, "interval": interval})
    return http.get_json(url, timeout=timeout, source=SOURCE)


def chart(symbol: str, interval: str, period1: int | None = None, period2: int | None = None,
          rng: str | None = None, timeout: float = 10) -> dict:
    """Raw v8 chart payload. Give either period1/period2 (epoch s) or rng ('5d', '1y'…)."""
    params = {"interval": interval, "includePrePost": "false", "events": ""}
    if period1 is not None:
        params.update(period1=int(period1), period2=int(period2 if period2 is not None else 9999999999))
    else:
        params["range"] = rng or "1d"
    url = f"{BASE}/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?" + http.qs(params)
    return http.get_json(url, timeout=timeout, source=SOURCE)


# ------------------------------------------------------------------ parse
def _period(meta: dict):
    reg = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    start, end = num(reg.get("start")), num(reg.get("end"))
    return (start, end) if start and end and end > start else None


def _price(value):
    """A positive price, else None (Yahoo sends 0 / null for a symbol it has no price for)."""
    v = num(value)
    return v if v is not None and v > 0 else None


def _quote_from_meta(meta: dict) -> dict:
    return {
        "last": _price(meta.get("regularMarketPrice")),
        "prev_close": _price(meta.get("chartPreviousClose")) or _price(meta.get("previousClose")),
        "ts": num(meta.get("regularMarketTime")),
        "open": None,                                   # not in the chart meta
        "high": num(meta.get("regularMarketDayHigh")),
        "low": num(meta.get("regularMarketDayLow")),
        "volume": num(meta.get("regularMarketVolume")),
        "name": meta.get("shortName") or meta.get("longName"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "period": _period(meta),
    }


def parse_spark(payload: dict) -> dict:
    """{symbol: {last, prev_close, ts, high, low, volume, name, period, spark:[[t, px]…]}}.

    Symbols Yahoo returned without a (positive) price are left out (the caller treats
    them as "Yahoo answered, but without a price for this symbol")."""
    spark_ = _section(payload, "spark")
    if spark_.get("error"):
        raise UpstreamError(SOURCE, None, f"spark error: {str(spark_['error'])[:160]}")
    out = {}
    for item in _rows(spark_.get("result")):
        resp = _first(item.get("response"))
        meta = _dict(resp.get("meta"))
        q = _quote_from_meta(meta)
        if q["last"] is None or not item.get("symbol"):
            continue
        closes = _list(_first(_dict(resp.get("indicators")).get("quote")).get("close"))
        points = []
        for t, c in zip(_list(resp.get("timestamp")), closes):
            c = num(c)
            if c is not None and t is not None:
                points.append([int(t), c])
        q["spark"] = points
        out[item["symbol"]] = q
    return out


def _local_day(t: int, zone, fallback_offset: float) -> int:
    """00:00 UTC of the exchange-local calendar date of a bar stamped `t`.

    The zone decides bar by bar (a series spans DST changes: CL=F daily bars are stamped
    04:00 UTC in summer and 05:00 UTC in winter, EURUSD=X 23:00 UTC of the day before in
    summer). Without any time-zone rule for the exchange, today's `gmtoffset` is used,
    and a bar stamped exactly one hour before local midnight — the mark of a bar from the
    other side of a DST change — is counted to the day that starts an hour later."""
    if zone is not None:
        local = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(zone)
        return int(dt.datetime(local.year, local.month, local.day,
                               tzinfo=dt.timezone.utc).timestamp())
    if (t + fallback_offset) % 86400 == 82800:
        return day_floor(t + 3600, fallback_offset)
    return day_floor(t, fallback_offset)


def _merge_same_t(rows: list) -> list:
    """Bars that fall on the same day/week become one: Yahoo appends today's bar to a
    weekly series as a separate trailing point (open/high/low of today only)."""
    out = []
    for r in rows:
        if out and out[-1]["t"] == r["t"]:
            b = out[-1]
            b["h"], b["l"], b["c"] = max(b["h"], r["h"]), min(b["l"], r["l"]), r["c"]
            b["v"] += r["v"]
        else:
            out.append(r)
    return out


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x) -> list:
    return x if isinstance(x, list) else []


def _rows(x) -> list:
    return [r for r in _list(x) if isinstance(r, dict)]


def _first(x) -> dict:
    x = _list(x)
    return _dict(x[0]) if x else {}


def _section(payload, name: str) -> dict:
    """payload[name] as a dict; anything else is not a Yahoo reply."""
    if not isinstance(payload, dict) or not isinstance(payload.get(name), dict):
        raise UpstreamError(SOURCE, None, f"unexpected reply shape (no '{name}' object)")
    return payload[name]


def parse_chart(payload: dict, tf: str) -> dict:
    """{"candles": [{t,o,h,l,c,v}…], "quote": {...meta quote...}, "has_volume": bool}.

    Rows with a null o/h/l/c are skipped. Daily/weekly `t` is 00:00 UTC of the bar's
    exchange-local date (weekly: its Monday), taken bar by bar from the exchange's time
    zone. Yahoo's trailing "live" point (stamped with the last trade time) is folded
    into the bar it belongs to."""
    chart_ = _section(payload, "chart")
    if chart_.get("error"):
        err = chart_["error"]
        raise UpstreamError(SOURCE, None, str(err.get("description") if isinstance(err, dict) else err)[:200])
    res = _first(chart_.get("result"))
    if not res:
        raise UpstreamError(SOURCE, None, "empty chart result")
    meta = _dict(res.get("meta"))
    q = _first(_dict(res.get("indicators")).get("quote"))
    ts = _list(res.get("timestamp"))
    cols = [_list(q.get(k)) for k in ("open", "high", "low", "close", "volume")]
    rows = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (num(col[i]) if i < len(col) else None for col in cols)
        t = num(t)
        if t is None or None in (o, h, l, c):
            continue
        rows.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v or 0.0})
    step = STEP[tf]
    if tf in ("1d", "1w"):
        off = num(meta.get("gmtoffset")) or 0
        zone = session.zone(meta.get("exchangeTimezoneName"))
        for r in rows:
            day = _local_day(r["t"], zone, off)
            r["t"] = week_floor(day) if tf == "1w" else day
        rows = _merge_same_t(rows)
    elif rows:
        live_t = num(meta.get("regularMarketTime"))
        last = rows[-1]
        # the live point is recognised by its phase: every real bar starts a whole number
        # of steps after the previous one (US hourly bars start at :30, so `t % step` alone
        # does not tell); a live point may well fall on a whole minute
        if len(rows) >= 2:
            off_grid = (last["t"] - rows[-2]["t"]) % step != 0
        else:
            off_grid = last["t"] % step != 0
        if live_t and last["t"] == int(live_t) and off_grid:
            if len(rows) >= 2:
                prev = rows[-2]
                k = (last["t"] - prev["t"]) // step
                if k <= 0:                      # the live point lies inside the previous bar
                    prev["c"] = last["c"]
                    prev["h"], prev["l"] = max(prev["h"], last["c"]), min(prev["l"], last["c"])
                    rows.pop()
                else:                           # first print of a new bar
                    last["t"] = prev["t"] + k * step
            else:
                last["t"] -= last["t"] % step
    return {"candles": rows, "quote": _quote_from_meta(meta),
            "has_volume": any(r["v"] for r in rows)}
