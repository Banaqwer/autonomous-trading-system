"""Normalised OHLCV for GET /api/candles (REBUILD_SPEC §3.4).

    {"id","tf","source","freshness","as_of","volume":bool,"candles":[{"t","o","h","l","c","v"}…]}

ascending, unique `t` (bar open, epoch s), no nulls, no synthetic bars, `limit` clamped
to 1..1000. Daily/weekly `t` is 00:00 UTC of the bar's exchange-local trading date
(weekly: its Monday). EOD-only instruments return daily closes as o=h=l=c with
"kind":"line". Sources per instrument come from core.universe (first that answers wins).
"""
from __future__ import annotations

import math
import time

from core import session, universe
from core.cache import cached
from core.errors import ApiError, BadRequest, NoSource, UpstreamError
from core.util import week_floor

TIMEFRAMES = universe.TIMEFRAMES
STEP = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1w": 604800}
TTL = {"1m": 10, "5m": 20, "15m": 30, "1h": 60, "1d": 300, "1w": 300}   # seconds; do not poll faster
TTL_CLOSED = 60                       # floor while the instrument's market is closed
MAX_LIMIT, DEFAULT_LIMIT = 1000, 300
_BUCKETS = (300, 1000)                # bars fetched upstream (the reply is sliced to `limit`)


def clamp_limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        raise BadRequest("limit must be an integer between 1 and 1000") from None
    return max(1, min(n, MAX_LIMIT))


def normalise(rows, decimals: int | None = None) -> list[dict]:
    """Ascending, unique t (the later row wins), rows with a null/NaN price dropped,
    null volume -> 0. Prices are rounded to `decimals`+3 places to strip float32 noise."""
    nd = None if decimals is None else decimals + 3
    by_t = {}
    for r in rows or []:
        try:
            t = int(r["t"])
            o, h, l, c = (float(r[k]) for k in "ohlc")
            v = float(r.get("v") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c)) or not math.isfinite(v):
            continue
        if nd is not None:
            o, h, l, c = round(o, nd), round(h, nd), round(l, nd), round(c, nd)
        by_t[t] = {"t": t, "o": o, "h": max(h, o, c), "l": min(l, o, c), "c": c, "v": v}
    return [by_t[t] for t in sorted(by_t)]


def weekly_line(points: list[dict]) -> list[dict]:
    """Daily {t, value} observations -> one point per week (its last observation)."""
    weeks = {}
    for p in sorted(points, key=lambda p: p["t"]):
        weeks[week_floor(p["t"])] = p["value"]
    return [{"t": t, "value": v} for t, v in sorted(weeks.items())]


def _line_rows(points: list[dict]) -> list[dict]:
    return [{"t": p["t"], "o": p["value"], "h": p["value"], "l": p["value"], "c": p["value"], "v": 0.0}
            for p in points]


# ------------------------------------------------------------ source fetchers
def _binance(inst, tf, n):
    from providers import binance
    rows = binance.candles(inst["vendor"]["binance"], tf, n)
    return rows, {"freshness": "RT"}


def _alpaca(inst, tf, n):
    from providers import alpaca
    rows = alpaca.parse_bars(alpaca.bars(inst["vendor"]["alpaca"], tf, n), tf)
    if not rows:
        raise UpstreamError("alpaca", None, f"no {tf} bars returned for {inst['id']}")
    if tf in ("1d", "1w"):
        return rows, {"freshness": "D15", "note": "consolidated tape (SIP), 15 minutes delayed"}
    return rows, {"freshness": "RT",
                  "note": "IEX venue only: prices can differ by cents from the consolidated "
                          "tape and volume is IEX volume"}


def _yahoo_bars_per_day(inst, tf) -> float:
    minutes = 1440 if inst["cls"] in ("future", "fx") else 390
    return {"1m": minutes, "5m": minutes / 5, "15m": minutes / 15, "1h": max(minutes / 60, 7)}[tf]


def _yahoo(inst, tf, n):
    from providers import yahoo
    sym, now = inst["vendor"]["yahoo"], time.time()
    if tf == "1d":
        spans = [(n * 7 / 5 + 12) * 86400]
    elif tf == "1w":
        spans = [(n * 7 + 21) * 86400]
    else:
        cap = yahoo.MAX_LOOKBACK_DAYS[tf] * 86400
        days = math.ceil(n / _yahoo_bars_per_day(inst, tf))
        tight = min(cap, n * STEP[tf] * 1.3 + 3600)             # enough when the market trades now
        wide = min(cap, (days * 7 / 5 + 3) * 86400)             # spans weekends / holidays
        spans = [tight, wide] if wide > tight * 1.5 else [wide]
    parsed = None
    for span in spans:
        parsed = yahoo.parse_chart(yahoo.chart(sym, yahoo.INTERVAL[tf], int(now - span), int(now + 86400)), tf)
        if len(parsed["candles"]) >= n:
            break
    if not parsed["candles"]:
        raise UpstreamError("yahoo", None, f"no {tf} bars returned for {inst['id']}")
    extra = {"freshness": universe.freshness(inst["id"], "yahoo")}
    if not parsed["has_volume"]:
        extra["volume"] = False
    name = parsed["quote"].get("name")
    if inst["cls"] == "future" and name:
        extra["note"] = f"front contract: {name}"
    return parsed["candles"], extra


def _treasury(inst, tf, n):
    from providers import treasury
    column, year = inst["vendor"]["treasury"], time.gmtime().tm_year
    need = n * (5 if tf == "1w" else 1)
    points, errors = [], []
    for back in range(0, 6):
        y = year - back
        try:
            rows = cached(f"treasury:curve:{y}", 1800 if back == 0 else 86400,
                          lambda y=y: {"rows": treasury.parse_curve(treasury.par_curve(y))},
                          stale_ttl=7 * 86400)["rows"]
        except ApiError as e:
            errors.append(e)
            if back == 0:
                continue            # early January: the new year's file may not exist yet
            break
        points = treasury.series(rows, column) + points
        if len(points) >= need:
            break
    if not points:
        raise errors[0] if errors else UpstreamError("treasury", None, "no observations")
    return points, {"freshness": "EOD", "kind": "line",
                    "note": "Treasury.gov daily par yield curve, end of day"}


def _bundesbank(inst, tf, n):
    from providers import bundesbank
    need = n * (5 if tf == "1w" else 1)
    points = bundesbank.parse_csv(bundesbank.series_csv(last_n=int(need * 1.5) + 10))
    if not points:
        raise UpstreamError("bundesbank", None, "no observations")
    return points, {"freshness": "EOD", "kind": "line",
                    "note": "Bundesbank fitted 10-year yield on listed Federal securities, end of day"}


_FETCH = {"binance": _binance, "alpaca": _alpaca, "yahoo": _yahoo,
          "treasury": _treasury, "bundesbank": _bundesbank}


def _build(inst, tf, n, source) -> dict:
    rows, extra = _FETCH[source](inst, tf, n)
    if extra.get("kind") == "line":
        if tf == "1w":
            rows = weekly_line(rows)
        rows = _line_rows(rows)
        extra["volume"] = False
    candles = normalise(rows, inst["decimals"])
    if not candles:
        raise UpstreamError(source, None, f"no usable {tf} bars for {inst['id']}")
    out = {"id": inst["id"], "tf": tf, "source": source, "freshness": extra["freshness"],
           "as_of": time.time(), "volume": extra.get("volume", True), "candles": candles}
    for k in ("kind", "note"):
        if extra.get(k):
            out[k] = extra[k]
    return out


def get(id_: str, tf: str = "1h", limit=DEFAULT_LIMIT) -> dict:
    """The /api/candles payload. Raises BadRequest / NoSource / UpstreamError."""
    inst = universe.require(id_)
    if tf not in TIMEFRAMES:
        raise BadRequest(f"tf must be one of {', '.join(TIMEFRAMES)}")
    n = clamp_limit(limit)
    if inst.get("candles_kind") == "line" and tf not in ("1d", "1w"):
        raise NoSource(f"{inst['id']} is end-of-day only ({inst['candles'][0]}): "
                       f"there are no intraday bars — use tf=1d or tf=1w")
    bucket = next(b for b in _BUCKETS if n <= b)
    ttl = TTL[tf]
    if session.state(inst["calendar"]) == session.CLOSED:
        ttl = max(ttl, TTL_CLOSED)
    last_error: Exception | None = None
    for source in inst["candles"]:
        try:
            payload = cached(f"{source}:candles:{inst['id']}:{tf}:{bucket}", ttl,
                             lambda s=source: _build(inst, tf, bucket, s))
        except ApiError as e:
            last_error = e
            continue
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError) as e:
            # the source sent something its parser could not read: a failure of that
            # source (502, and the next source is tried) — not a bug of the gateway (500)
            last_error = UpstreamError(source, None, f"unexpected reply shape ({type(e).__name__})")
            continue
        out = dict(payload)
        out["candles"] = payload["candles"][-n:]
        if last_error is not None and "note" not in out:
            out["note"] = f"fallback source: {last_error}"
        return out
    raise last_error or NoSource(f"no candle source for {inst['id']}")
