"""FLOW routes (S2): real order flow for BTC, ETH and SOL (Binance spot). Nothing else in
the universe has a free order-book or trade-by-trade source: every other id answers 404
`no_source` with the reason (unknown id: 400). An id is never replaced by another one.

    GET /api/book?id=&depth=                 depth ladder, best first
    GET /api/trades?id=&limit=               time & sales, oldest first
    GET /api/book/history?id=&since=&until=  heatmap history: resting size per price bucket over time
    GET /api/footprint?id=&tf=&step=&limit=&since=&rows=   footprint bars: sell x buy volume per price step

Where the data comes from
* Stream (no upstream cost): the hub's Binance events — `depth20@100ms` books and every
  aggTrade — are watched through `HUB.on_hub`. Kept in memory, bounded:
    - the best 20 levels per side once per second for HISTORY_S seconds (book history "top");
    - every trade folded into one-minute footprint cells (MINUTES_KEPT minutes), into
      per-second buy/sell volume (HISTORY_S) and into a tail of the last TAIL_MAX trades.
* REST (keyless Binance, weight-limited per IP — every call goes through core.cache):
    - `depth?limit=1000` (weight 50), cached DEEP_TTL s: the ladder beyond 20 levels and
      the heatmap's "deep" columns. A background sampler asks for it every DEEP_EVERY s for
      an instrument whose book page is open, every DEEP_IDLE_EVERY s for WANT_LINGER s after
      the last request, and not at all otherwise.
    - `aggTrades`: the tape when the stream is not live, and the footprint backfill
      (BACKFILL_S seconds before the first stream trade, and any hole left by a stream
      reconnect), fetched in pages by a background worker only for instruments whose
      footprint was asked for in the last WANT_LINGER s.
  Everything is lost when the gateway restarts; a page reload keeps it.

Trade times are Binance's own (`T`, ms). The stream and REST deliver the same aggTrades
(checked on 2026-11-12: 458 of 458 in one window, identical volume), so a hole is filled
by time window, exclusive of the milliseconds the stream already holds.

Background threads do not start when OT_FLOW_BG=0 or OT_QUOTES=0 (tests).
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Query

import stream_hub
from core import universe
from core.cache import cached
from core.errors import ApiError, BadRequest, UpstreamError, describe
from core.fanout import HUB
from providers import binance

log = logging.getLogger("uvicorn.error").getChild("flow")
router = APIRouter()

SOURCE = "binance"
TICK = 0.01                   # PRICE_FILTER.tickSize of BTCUSDT, ETHUSDT and SOLUSDT (exchangeInfo, 2026-11-12)
STREAM_LEVELS = 20            # levels per side in the hub's book events
DEPTH_MAX = 1000              # /api/book depth clamp (limit 5000 would cost weight 250 per call)
SHALLOW_TTL = 1.0             # REST depth with limit <= 100 (weight 5)
DEEP_TTL = 5.0                # REST depth with limit 500 / 1000 (weight 25 / 50)
TRADES_TTL = 2.0              # REST aggTrades tail (weight 4)
HISTORY_S = 1800              # book history and per-second trade volume kept, seconds
BAND = 0.0075                 # deep columns keep +-0.75 % around the mid (or as far as 1000 levels reach)
DEEP_EVERY = 5.0              # sampler cadence for an instrument a page is showing
DEEP_IDLE_EVERY = 30.0        # ... for one that was shown in the last WANT_LINGER seconds
ACTIVE_FOR = 20.0             # "a page is showing it" = asked within this many seconds
WANT_LINGER = 1800.0          # background work for an instrument stops this long after the last request
MINUTES_KEPT = 360            # footprint minutes kept per instrument
BACKFILL_S = 3600             # footprint backfill target: this long before the head of the tape
BACKFILL_CHUNK_S = 120        # the backfill walks backwards in windows of this size
TAIL_MAX = 1000               # raw stream trades kept for /api/trades
STREAM_GAP = 3.0              # no Binance event for an instrument for this long = the stream session broke
TRADES_MAX = 1000
AGG_PAGE = 1000               # aggTrades rows per call (Binance's maximum)
TF = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
STEP_MULTS = (1, 2, 5, 10, 20, 50, 100)
BARS_MAX = 120


# ------------------------------------------------------------------ small pure helpers
def _ticks(price: float) -> int:
    return int(round(price / TICK))


def _px(ticks: int) -> float:
    return round(ticks * TICK, 2)


def _sig(v: float) -> float:
    """4 significant digits: sizes in the history payload do not need more."""
    return float("%.4g" % v) if v else 0.0


def nice_step(x: float) -> float:
    """Smallest 1-2-5 x 10^n that is >= x."""
    if not x or x <= 0 or not math.isfinite(x):
        return TICK
    base = 10.0 ** math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if m * base >= x * (1 - 1e-9):
            return m * base
    return 10 * base


def heat_step_ticks(mid: float) -> int:
    """Price bucket of the book history: about 2.5 bp of the price, 1-2-5, never below a tick."""
    return max(1, int(round(nice_step(mid * 2.5e-5) / TICK)))


def foot_base_ticks(price: float) -> int:
    """Finest footprint price step: a power of ten near 2 bp of the price (BTC 1, ETH 0.01, SOL 0.01)."""
    if not price or price <= 0:
        return 1
    return max(1, int(round(10.0 ** math.floor(math.log10(price * 2e-5)) / TICK)))


def bucket_levels(bids, asks, step_ticks: int, band: Optional[float] = None,
                  full_bids: bool = False, full_asks: bool = False):
    """Sum level sizes into price buckets; bucket k covers [k*step, (k+1)*step).

    Returns (k0, [size…]) for the buckets that are COMPLETE: the bucket holding the last
    level of a truncated side is left out (levels beyond the cut are unknown, its sum
    would be too small). `band` limits the range to mid*(1 +- band)."""
    if not bids or not asks:
        return None, []
    lo = _ticks(bids[-1][0]) // step_ticks + (0 if full_bids else 1)
    hi = _ticks(asks[-1][0]) // step_ticks - (0 if full_asks else 1)
    if band:
        mid = (bids[0][0] + asks[0][0]) / 2.0
        lo = max(lo, _ticks(mid * (1 - band)) // step_ticks)
        hi = min(hi, _ticks(mid * (1 + band)) // step_ticks)
    if hi < lo:
        return None, []
    q = [0.0] * (hi - lo + 1)
    for side in (bids, asks):
        for p, s in side:
            k = _ticks(p) // step_ticks
            if lo <= k <= hi:
                q[k - lo] += s
    return lo, [_sig(v) for v in q]


def _stream_state() -> str:
    try:
        return stream_hub.snapshot()["feeds"].get("binance", {}).get("state") or "off"
    except Exception:                                       # the hub is not running (tests)
        return "off"


def bg_enabled() -> bool:
    return os.environ.get("OT_FLOW_BG", "1").strip() != "0" and os.environ.get("OT_QUOTES", "1").strip() != "0"


# ------------------------------------------------------------------ book history
class BookHistory:
    """Resting size per price bucket over time for one instrument (bounded: HISTORY_S)."""

    def __init__(self, id_: str):
        self.id = id_
        self.lock = threading.Lock()
        self.step_ticks: Optional[int] = None
        self.deep: deque = deque()          # {"t", "bid", "ask", "k0", "q"}
        self.top: deque = deque()           # (sec, bid, ask, k0, q)
        self.pending = None                 # (sec, bids, asks): latest stream book of the running second
        self.wanted_at = 0.0
        self.active_at = 0.0
        self.sampled_at = 0.0
        self.retry_at = 0.0
        self.error: Optional[str] = None
        self.deep_since: Optional[float] = None

    def _step(self, mid: float) -> int:
        if self.step_ticks is None:
            self.step_ticks = heat_step_ticks(mid)
        return self.step_ticks

    def _evict(self, now: float) -> None:
        cut = now - HISTORY_S
        while self.deep and self.deep[0]["t"] < cut:
            self.deep.popleft()
        while self.top and self.top[0][0] < cut:
            self.top.popleft()
        if not self.deep:
            self.deep_since = None
        elif self.deep_since is not None and self.deep_since < self.deep[0]["t"]:
            self.deep_since = self.deep[0]["t"]

    def _top_entry(self, pending):
        sec, bids, asks = pending
        if not bids or not asks:
            return None
        step = self._step((bids[0][0] + asks[0][0]) / 2.0)
        k0, q = bucket_levels(bids, asks, step, None, len(bids) < STREAM_LEVELS, len(asks) < STREAM_LEVELS)
        return (sec, bids[0][0], asks[0][0], k0, q)

    def on_book(self, bids, asks, now: float) -> None:
        """A stream book (event-loop thread): keep the last one of every second."""
        sec = int(now)
        with self.lock:
            p = self.pending
            if p is not None and p[0] != sec:
                entry = self._top_entry(p)
                if entry is not None:
                    self.top.append(entry)
                self._evict(now)
            self.pending = (sec, bids, asks)

    def record_deep(self, bids, asks, t: float, limit: int) -> bool:
        """A fresh REST depth snapshot becomes one heatmap column."""
        if not bids or not asks:
            return False
        with self.lock:
            if self.deep and t <= self.deep[-1]["t"]:
                return False
            step = self._step((bids[0][0] + asks[0][0]) / 2.0)
            k0, q = bucket_levels(bids, asks, step, BAND, len(bids) < limit, len(asks) < limit)
            if k0 is None:
                return False
            if self.deep_since is None or t - self.deep[-1]["t"] > 2 * DEEP_IDLE_EVERY + DEEP_TTL:
                self.deep_since = t                      # a new uninterrupted run starts here
            self.deep.append({"t": round(t, 3), "bid": bids[0][0], "ask": asks[0][0], "k0": k0, "q": q})
            self._evict(t)
            self.error = None
            return True

    def has_deep(self, now: float) -> bool:
        with self.lock:
            self._evict(now)
            return bool(self.deep)

    def want(self, now: float, active: bool = True) -> None:
        self.wanted_at = now
        if active:
            self.active_at = now

    def payload(self, since: float, until: float, now: float) -> dict:
        with self.lock:
            self._evict(now)
            deep = [c for c in self.deep if since < c["t"] <= until]
            top = [list(e) for e in self.top if since < e[0] <= until]
            p = self.pending
            if p is not None and since < p[0] <= until and now - p[0] < STREAM_GAP + 1:
                entry = self._top_entry(p)
                if entry is not None:
                    top.append(list(entry))
            first_top = self.top[0][0] if self.top else (p[0] if p else None)
            last_top = p[0] if p is not None else (self.top[-1][0] if self.top else None)
            info = {"deep_since": self.deep_since, "deep_first": self.deep[0]["t"] if self.deep else None,
                    "deep_last": self.deep[-1]["t"] if self.deep else None, "deep_n": len(self.deep),
                    "top_since": first_top, "top_last": last_top, "top_n": len(self.top),
                    "error": self.error}
            step = self.step_ticks
        return {"deep": deep, "top": top, "info": info, "step_ticks": step}


# ------------------------------------------------------------------ trades / footprint
class _Minute:
    __slots__ = ("o", "h", "l", "c", "n", "buy", "sell", "first", "last", "cells")

    def __init__(self):
        self.o = self.h = self.l = self.c = None
        self.n = 0
        self.buy = self.sell = 0.0
        self.first = self.last = None        # trade times (ms) that set o and c
        self.cells = {}                      # base bucket -> [sell, buy]


class TradeStore:
    """Every known trade of one instrument, folded into one-minute cells (bounded: MINUTES_KEPT).

    `intervals` are the stretches of Binance time (ms, inclusive) for which ALL trades are
    held; `seg` is the one the live stream is extending."""

    def __init__(self, id_: str):
        self.id = id_
        self.lock = threading.Lock()
        self.base_ticks: Optional[int] = None
        self.minutes = {}                    # minute start (s) -> _Minute
        self.secs = {}                       # second -> [buy qty, sell qty, buy notional, sell notional]
        self.tail: deque = deque(maxlen=TAIL_MAX)
        self.intervals = []                  # [[a_ms, b_ms], …] sorted, disjoint
        self.seg = None
        self.alive_at = 0.0                  # local time of the last stream event for this instrument
        self.skew_ms = 0.0                   # local clock - Binance trade time (includes transit)
        self.last_ms: Optional[int] = None   # newest trade held
        self.rest_last_id = None
        self.wanted_at = 0.0
        self.retry_at = 0.0
        self.followed_at = 0.0               # last REST "follow the head" call (no stream)
        self.backfill = "idle"               # idle | running | done | error
        self.error: Optional[str] = None
        self.n_trades = 0

    # -- coverage ---------------------------------------------------------
    def _inside(self, ms: int) -> bool:
        for a, b in self.intervals:
            if a <= ms <= b:
                return True
        return False

    def _mark(self, a: int, b: int) -> list:
        new, live, out = [a, b], False, []
        for iv in self.intervals:
            if iv[1] < new[0] - 1 or iv[0] > new[1] + 1:
                out.append(iv)
            else:
                new[0], new[1] = min(new[0], iv[0]), max(new[1], iv[1])
                live = live or iv is self.seg
        out.append(new)
        out.sort(key=lambda iv: iv[0])
        self.intervals = out
        if live:
            self.seg = new
        return new

    def live(self, now: float) -> bool:
        return self.seg is not None and now - self.alive_at <= STREAM_GAP

    def head_ms(self, now: float) -> Optional[int]:
        """Up to which Binance time the tape is known (the live stream: "now" over there)."""
        if not self.intervals:
            return None
        end = self.intervals[-1][1]
        if self.live(now) and self.intervals[-1] is self.seg:
            end = max(end, int(now * 1000 - self.skew_ms))
        return end

    def covered(self, a: int, b: int, now: float) -> bool:
        head = self.head_ms(now)
        for iv in self.intervals:
            end = head if iv is self.intervals[-1] and head is not None else iv[1]
            if iv[0] <= a and b <= end:
                return True
        return False

    def next_gap(self, now: float):
        """The newest hole (a_ms, b_ms) in the backfill target, or None."""
        if not self.intervals:
            return None
        head = self.head_ms(now)
        target = head - BACKFILL_S * 1000
        oldest_kept = (int(now // 60) - MINUTES_KEPT + 1) * 60000
        target = max(target, oldest_kept)
        ivs = self.intervals
        for i in range(len(ivs) - 1, 0, -1):
            a, b = ivs[i - 1][1] + 1, ivs[i][0] - 1
            if b >= a and b > target:
                return max(a, target), b
        if ivs[0][0] - 1 > target:
            return target, ivs[0][0] - 1
        return None

    # -- ingest -----------------------------------------------------------
    def _add(self, ms: int, px: float, qty: float, buy: bool) -> None:
        if self.base_ticks is None:
            self.base_ticks = foot_base_ticks(px)
        m = ms // 60000 * 60
        bar = self.minutes.get(m)
        if bar is None:
            bar = self.minutes[m] = _Minute()
            if len(self.minutes) > MINUTES_KEPT + 8:
                self._evict_minutes()
        if bar.first is None or ms < bar.first:
            bar.first, bar.o = ms, px
        if bar.last is None or ms >= bar.last:
            bar.last, bar.c = ms, px
        bar.h = px if bar.h is None or px > bar.h else bar.h
        bar.l = px if bar.l is None or px < bar.l else bar.l
        bar.n += 1
        cell = bar.cells.get(_ticks(px) // self.base_ticks)
        if cell is None:
            cell = bar.cells[_ticks(px) // self.base_ticks] = [0.0, 0.0]
        if buy:
            cell[1] += qty
            bar.buy += qty
        else:
            cell[0] += qty
            bar.sell += qty
        if self.last_ms is None or ms >= self.last_ms - HISTORY_S * 1000:      # per-second volume: the heatmap's window only
            sec = self.secs.get(ms // 1000)
            if sec is None:
                sec = self.secs[ms // 1000] = [0.0, 0.0, 0.0, 0.0]
                if len(self.secs) > HISTORY_S + 600:
                    cut = max(self.secs) - HISTORY_S
                    for k in [k for k in self.secs if k < cut]:
                        del self.secs[k]
            if buy:
                sec[0] += qty
                sec[2] += qty * px
            else:
                sec[1] += qty
                sec[3] += qty * px
        if self.last_ms is None or ms > self.last_ms:
            self.last_ms = ms
        self.n_trades += 1

    def _evict_minutes(self) -> None:
        keys = sorted(self.minutes)
        for k in keys[:max(0, len(keys) - MINUTES_KEPT)]:
            del self.minutes[k]
        if not self.minutes:
            return
        floor_ms = min(self.minutes) * 1000
        kept = []
        for iv in self.intervals:
            if iv[1] < floor_ms:
                continue
            iv[0] = max(iv[0], floor_ms)
            kept.append(iv)
        self.intervals = kept

    def add_stream(self, ms: int, px: float, qty: float, buy: bool, now: float) -> bool:
        """One stream aggTrade (event-loop thread)."""
        with self.lock:
            if self.seg is not None and now - self.alive_at > STREAM_GAP:
                self.seg = None                           # the session broke: what follows is a new stretch
            if self.seg is None:
                if self._inside(ms):                      # REST already holds this millisecond
                    self.alive_at = now
                    return False
                self.seg = self._mark(ms, ms)             # may continue a stretch REST ended one ms earlier
            elif ms < self.seg[0]:
                return False
            elif ms > self.seg[1]:
                self.seg[1] = ms
            self.alive_at = now
            lag = now * 1000 - ms
            self.skew_ms = lag if not self.skew_ms else 0.9 * self.skew_ms + 0.1 * lag
            self._add(ms, px, qty, buy)
            self.tail.append((ms, px, qty, buy))
            return True

    def touch_stream(self, now: float) -> None:
        """Any other stream event for this instrument: the session is alive (quiet tape)."""
        with self.lock:
            if self.seg is not None:
                if now - self.alive_at > STREAM_GAP:
                    self.seg = None
                else:
                    self.alive_at = now

    def add_rest(self, trades, a: Optional[int] = None, b: Optional[int] = None, trust: bool = False) -> int:
        """REST aggTrades (ascending). Everything in [a, b] (ms; default: first..last trade)
        is known afterwards. Trades in stretches already held are skipped — unless `trust`
        (a page that continues the head by aggTrade id: every row in it is new)."""
        n = 0
        with self.lock:
            for tr in trades:
                ms = int(round(tr["t"] * 1000))
                if not trust and ((a is not None and ms < a) or (b is not None and ms > b) or self._inside(ms)):
                    continue
                self._add(ms, tr["px"], tr["qty"], bool(tr["buy"]))
                n += 1
            if a is None and trades:
                a = int(round(trades[0]["t"] * 1000))
            if b is None and trades:
                b = int(round(trades[-1]["t"] * 1000))
            if a is not None and b is not None and b >= a:
                self._mark(a, b)
            if trades and trades[-1].get("id") is not None:
                if self.last_ms is not None and int(round(trades[-1]["t"] * 1000)) >= self.last_ms:
                    self.rest_last_id = trades[-1]["id"]
        return n

    # -- read -------------------------------------------------------------
    def snapshot(self, from_s: int):
        """Copies of the minutes at or after `from_s` (cheap: the caller aggregates outside the lock)."""
        with self.lock:
            out = {}
            for m, bar in self.minutes.items():
                if m >= from_s:
                    out[m] = (bar.o, bar.h, bar.l, bar.c, bar.n, bar.buy, bar.sell, bar.first, bar.last,
                              {k: (v[0], v[1]) for k, v in bar.cells.items()})
            return out, list(map(list, self.intervals)), self.base_ticks, self.last_ms

    def seconds(self, since: float, until: float):
        with self.lock:
            rows = []
            for s in sorted(k for k in self.secs if since < k <= until):
                b, sl, bn, sn = self.secs[s]
                rows.append([s, _sig(b), _sig(sl), round(bn / b, 2) if b else None, round(sn / sl, 2) if sl else None])
            return rows


_BOOKS: dict = {}
_TRADES: dict = {}
_stores_lock = threading.Lock()


def book_history(id_: str) -> BookHistory:
    h = _BOOKS.get(id_)
    if h is None:
        with _stores_lock:
            h = _BOOKS.setdefault(id_, BookHistory(id_))
    return h


def trade_store(id_: str) -> TradeStore:
    s = _TRADES.get(id_)
    if s is None:
        with _stores_lock:
            s = _TRADES.setdefault(id_, TradeStore(id_))
    return s


def reset() -> None:
    """Forget everything recorded (tests)."""
    with _stores_lock:
        _BOOKS.clear()
        _TRADES.clear()


# ------------------------------------------------------------------ hub hooks (event-loop thread: never block)
def _on_hub_book(ev: dict) -> None:
    if ev.get("src") != SOURCE:
        return
    id_ = universe.from_vendor(SOURCE, ev.get("sym"))
    if id_ is None:
        return
    now = time.time()
    book_history(id_).on_book(ev.get("bids") or [], ev.get("asks") or [], now)
    trade_store(id_).touch_stream(now)


def _on_hub_trade(ev: dict) -> None:
    if ev.get("src") != SOURCE:
        return
    id_ = universe.from_vendor(SOURCE, ev.get("sym"))
    if id_ is None:
        return
    trade_store(id_).add_stream(int(ev["t"]), float(ev["px"]), float(ev["qty"]), bool(ev["buy"]), time.time())


HUB.on_hub("book", _on_hub_book)
HUB.on_hub("trade", _on_hub_trade)


# ------------------------------------------------------------------ upstream calls (all through core.cache)
def _depth_limit(depth: int) -> int:
    for limit in binance.DEPTH_LIMITS:
        if limit >= depth:
            return limit
    return DEPTH_MAX


def rest_book(inst: dict, limit: int, ttl: Optional[float] = None) -> dict:
    """One cached REST depth snapshot. A fresh 1000-level one is also a heatmap column."""
    sym = universe.vendor(inst["id"], SOURCE)
    if ttl is None:
        ttl = DEEP_TTL if limit > 100 else SHALLOW_TTL

    def load():
        d = binance.depth_snapshot(sym, limit)
        if not d.get("bids") or not d.get("asks"):
            raise UpstreamError(SOURCE, None, f"empty order book in the reply for {sym}")
        now = time.time()
        if limit >= DEPTH_MAX:
            book_history(inst["id"]).record_deep(d["bids"], d["asks"], now, limit)
        return {"id": inst["id"], "source": SOURCE, "freshness": "RT", "as_of": now, "via": "rest",
                "limit": limit, "bids": d["bids"], "asks": d["asks"]}
    return cached(f"binance:depth:{inst['id']}:{limit}", ttl, load, stale_ttl=30 if limit > 100 else 15)


def rest_trades(inst: dict) -> dict:
    sym = universe.vendor(inst["id"], SOURCE)

    def load():
        rows = binance.agg_trades(sym, TRADES_MAX)
        if not rows:
            raise UpstreamError(SOURCE, None, f"no trades in the reply for {sym}")
        return {"id": inst["id"], "source": SOURCE, "freshness": "RT", "as_of": time.time(), "via": "rest",
                "trades": rows}
    return cached(f"binance:aggTrades:{inst['id']}:latest", TRADES_TTL, load, stale_ttl=30)


def _ingest_page(store: TradeStore, sym: str, a: Optional[int] = None, b: Optional[int] = None,
                 from_id=None, ttl: float = 30.0) -> dict:
    """One aggTrades page into the store; the cache keeps only the summary.

    a and b      a closed window (backfill): everything in it is known afterwards; a full
                 page (1000 rows) covers the window only up to the millisecond before its
                 last trade (that millisecond may be cut in two — the next page starts there)
    a only       follow the head by time: known from a to the last trade returned
    from_id      follow the head by aggTrade id: known from the old head to the last trade
    nothing      the newest 1000 trades: a first stretch"""
    def load():
        if from_id is not None:
            head = store.intervals[-1][1] if store.intervals else None
            rows = binance.agg_trades(sym, AGG_PAGE, from_id=from_id)
            n = store.add_rest(rows, head, None, trust=True) if rows else 0
        elif a is not None and b is not None:
            rows = binance.agg_trades(sym, AGG_PAGE, start_ms=a, end_ms=b)
            end = b
            if len(rows) >= AGG_PAGE:
                first_ms, last_ms = int(round(rows[0]["t"] * 1000)), int(round(rows[-1]["t"] * 1000))
                if last_ms > first_ms:
                    end = last_ms - 1
            n = store.add_rest(rows, a, end)
        elif a is not None:
            rows = binance.agg_trades(sym, AGG_PAGE, start_ms=a)
            n = store.add_rest(rows, a, None) if rows else 0
        else:
            rows = binance.agg_trades(sym, AGG_PAGE)
            n = store.add_rest(rows)
        return {"n": n, "rows": len(rows)}
    return cached(f"binance:aggTrades:{store.id}:{a}:{b}:{from_id}", ttl, load, stale_ttl=0)


def trade_step(store: TradeStore, now: float) -> bool:
    """One unit of background work for a wanted instrument. True when something was fetched."""
    sym = universe.vendor(store.id, SOURCE)
    if not store.intervals:
        _ingest_page(store, sym)                         # the newest 1000 trades: a first stretch
        return True
    if not store.live(now):                              # no stream: follow the head by REST
        head = store.intervals[-1][1]
        if now - store.followed_at >= TRADES_TTL:
            store.followed_at = now
            if store.rest_last_id is not None and store.last_ms == head:
                _ingest_page(store, sym, from_id=store.rest_last_id + 1, ttl=TRADES_TTL)
            else:
                _ingest_page(store, sym, head + 1, ttl=TRADES_TTL)
            return True
    gap = store.next_gap(now)
    if gap is None:
        store.backfill = "done"
        return False
    a, b = gap
    store.backfill = "running"
    _ingest_page(store, sym, max(a, b - BACKFILL_CHUNK_S * 1000 + 1), b)
    return True


def _backoff(err: dict) -> float:
    """Seconds to leave Binance alone after an error ({code, message, retry_after?})."""
    wait = max(10.0, float(err.get("retry_after") or 0.0))
    text = str(err.get("message") or "")
    if err.get("code") == "rate_limited" or "HTTP 418" in text or "HTTP 429" in text:
        wait = max(wait, 120.0)                          # 429 = slow down, 418 = banned for a while: do not insist
    return wait


def sample_books(now: float) -> int:
    """The deep-book sampler's tick: one REST snapshot per due instrument."""
    n = 0
    for h in list(_BOOKS.values()):
        if now - h.wanted_at > WANT_LINGER or now < h.retry_at:
            continue
        every = DEEP_EVERY if now - h.active_at <= ACTIVE_FOR else DEEP_IDLE_EVERY
        if now - h.sampled_at < every - 0.05:
            continue
        h.sampled_at = now
        inst = universe.get(h.id)
        try:
            d = rest_book(inst, DEPTH_MAX, ttl=DEEP_EVERY - 1.0)
            if d.get("stale"):                           # the refresh failed, the cache served the old snapshot
                err = d.get("error") or {}
                h.error = err.get("message") or "the source failed"
                h.retry_at = now + _backoff(err)
            n += 1
        except ApiError as e:
            h.error = describe(e).get("message")
            h.retry_at = now + _backoff(describe(e))
        except Exception:                                 # a bug must not kill the thread
            log.exception("deep book sample for %s failed", h.id)
            h.retry_at = now + 30
    return n


def backfill_trades(now: float) -> bool:
    did = False
    for store in list(_TRADES.values()):
        if now - store.wanted_at > WANT_LINGER or now < store.retry_at:
            continue
        try:
            did = trade_step(store, now) or did
            store.error = None
        except ApiError as e:
            store.backfill, store.error = "error", describe(e).get("message")
            store.retry_at = now + _backoff(describe(e))
        except Exception:
            log.exception("trade backfill for %s failed", store.id)
            store.backfill, store.error = "error", "internal error (see the gateway log)"
            store.retry_at = now + 30
    return did


_threads_lock = threading.Lock()
_threads: dict = {}


def _loop_books():
    while True:
        try:
            sample_books(time.time())
        except Exception:
            log.exception("book sampler tick failed")
        time.sleep(0.5)


def _loop_trades():
    while True:
        did = False
        try:
            did = backfill_trades(time.time())
        except Exception:
            log.exception("trade backfill tick failed")
        time.sleep(0.25 if did else 1.0)


def ensure_workers() -> None:
    """Start the two daemon threads on first use (never in tests: see bg_enabled)."""
    if not bg_enabled():
        return
    with _threads_lock:
        for name, target in (("flow-book-sampler", _loop_books), ("flow-trade-backfill", _loop_trades)):
            t = _threads.get(name)
            if t is None or not t.is_alive():
                t = _threads[name] = threading.Thread(target=target, name=name, daemon=True)
                t.start()


# ------------------------------------------------------------------ routes
def _levels(side, depth: int):
    return [[p, q] for p, q in side[:depth]]


def _book_payload(d: dict, depth: int) -> dict:
    bids, asks = _levels(d["bids"], depth), _levels(d["asks"], depth)
    out = {k: v for k, v in d.items() if k not in ("bids", "asks", "type", "t")}
    bid, ask = bids[0][0], asks[0][0]
    out.update({"depth": min(len(bids), len(asks)), "tick": TICK, "best_bid": bid, "best_ask": ask,
                "mid": round((bid + ask) / 2.0, 4), "spread": round(ask - bid, 2), "bids": bids, "asks": asks})
    return out


@router.get("/api/book")
def book(id: str = Query(..., min_length=1, max_length=16, description="terminal id: BTC, ETH or SOL"),
         depth: int = Query(STREAM_LEVELS, description="levels per side, clamped to 1..1000")):
    """Depth ladder. Up to 20 levels come from the live stream when it is fresh; anything
    deeper (or a silent stream) from a cached REST snapshot."""
    inst = universe.require_cap(id, "book")
    depth = max(1, min(int(depth), DEPTH_MAX))
    now = time.time()
    if depth > 100:
        book_history(inst["id"]).want(now)               # a page shows this book: record its history too
        ensure_workers()
    if depth <= STREAM_LEVELS:
        live = HUB.latest_book(inst["id"])
        if live and live.get("bids") and live.get("asks"):
            return _book_payload({"id": inst["id"], "source": SOURCE, "freshness": "RT", "as_of": live["t"],
                                  "via": "stream", "limit": STREAM_LEVELS,
                                  "bids": live["bids"], "asks": live["asks"]}, depth)
    return _book_payload(rest_book(inst, _depth_limit(depth)), depth)


@router.get("/api/trades")
def trades(id: str = Query(..., min_length=1, max_length=16),
           limit: int = Query(100, description="clamped to 1..1000")):
    """Time & sales, oldest first: the stream's own tail while it is live and long enough,
    else the newest REST aggTrades (cached)."""
    inst = universe.require_cap(id, "trades")
    limit = max(1, min(int(limit), TRADES_MAX))
    store, now = trade_store(inst["id"]), time.time()
    if store.live(now):
        with store.lock:
            tail = list(store.tail)
        if len(tail) >= limit:
            rows = [{"t": ms / 1000.0, "px": px, "qty": qty, "buy": buy} for ms, px, qty, buy in tail[-limit:]]
            return {"id": inst["id"], "source": SOURCE, "freshness": "RT", "as_of": rows[-1]["t"],
                    "via": "stream", "trades": rows}
    d = rest_trades(inst)
    return {**d, "trades": d["trades"][-limit:]}


@router.get("/api/book/history")
def book_history_route(id: str = Query(..., min_length=1, max_length=16),
                       since: Optional[float] = Query(None, description="epoch s, exclusive"),
                       until: Optional[float] = Query(None, description="epoch s, inclusive")):
    """What the heatmap draws: `deep` columns (REST depth, 1000 levels, +-0.75 %), `top`
    (the stream's best 20 levels once per second), per-second trade volume. Bucket k of a
    column covers prices [k*step, (k+1)*step); `q[i]` is the size resting in bucket k0+i."""
    inst = universe.require_cap(id, "book")
    now = time.time()
    lo = now - HISTORY_S
    since = lo if since is None or not math.isfinite(since) else max(lo, min(float(since), now))
    ahead = now + 2.0                                     # a column stamped while this request runs still belongs to it
    until = ahead if until is None or not math.isfinite(until) else max(since, min(float(until), ahead))
    h = book_history(inst["id"])
    h.want(now)
    ensure_workers()
    if not h.has_deep(now):
        h.sampled_at = now
        try:                                             # first request: wait for the first column (cached, single-flight:
            rest_book(inst, DEPTH_MAX)                   # it joins the sampler's own call) instead of an empty screen
        except ApiError as e:
            h.error = describe(e).get("message")
        now = time.time()
    p = h.payload(since, until, now)
    step_ticks = p["step_ticks"]
    info = p["info"]
    stream = _stream_state()
    known = [t for t in (info["deep_last"], info["top_last"]) if t is not None]
    last = max(known) if known else None
    return {"id": inst["id"], "source": SOURCE, "freshness": "RT", "as_of": last,
            "server_time": now, "since": since, "until": until,
            "tick": TICK, "step": _px(step_ticks) if step_ticks else None,
            "band_pct": BAND * 100, "retention_s": HISTORY_S, "depth_levels": DEPTH_MAX,
            "deep_every_s": DEEP_EVERY, "deep_idle_every_s": DEEP_IDLE_EVERY, "linger_s": WANT_LINGER,
            "stream": stream, "recording": info,
            "deep": p["deep"], "top": p["top"],
            "trades": trade_store(inst["id"]).seconds(since, until)}


def _step_options(base_ticks: int):
    return [base_ticks * m for m in STEP_MULTS]


def build_bars(minutes: dict, base_ticks: int, tf_s: int, step_ticks: int, first_t: int, last_t: int):
    """Fold one-minute cells into `tf_s` bars and `step_ticks` price steps (pure)."""
    ratio = step_ticks // base_ticks
    bars = {}
    for m in sorted(minutes):
        t = m // tf_s * tf_s
        if t < first_t or t > last_t:
            continue
        o, h, l, c, n, buy, sell, first, last, cells = minutes[m]
        bar = bars.get(t)
        if bar is None:
            bar = bars[t] = {"t": t, "o": o, "h": h, "l": l, "c": c, "n": 0, "buy": 0.0, "sell": 0.0,
                             "_first": first, "_last": last, "_cells": {}}
        else:
            if first < bar["_first"]:
                bar["_first"], bar["o"] = first, o
            if last >= bar["_last"]:
                bar["_last"], bar["c"] = last, c
            bar["h"], bar["l"] = max(bar["h"], h), min(bar["l"], l)
        bar["n"] += n
        bar["buy"] += buy
        bar["sell"] += sell
        acc = bar["_cells"]
        for k, (s, b) in cells.items():
            kk = k // ratio
            cell = acc.get(kk)
            if cell is None:
                acc[kk] = [s, b]
            else:
                cell[0] += s
                cell[1] += b
    out = []
    for t in sorted(bars):
        bar = bars[t]
        cells = bar.pop("_cells")
        bar.pop("_first"), bar.pop("_last")
        rows, poc, best = [], None, -1.0
        for kk in sorted(cells):
            s, b = cells[kk]
            price = _px(kk * step_ticks)
            rows.append([price, round(s, 6), round(b, 6)])
            if s + b > best:
                best, poc = s + b, price
        bar.update({"buy": round(bar["buy"], 6), "sell": round(bar["sell"], 6),
                    "vol": round(bar["buy"] + bar["sell"], 6), "delta": round(bar["buy"] - bar["sell"], 6),
                    "poc": poc, "cells": rows})
        out.append(bar)
    return out


@router.get("/api/footprint")
def footprint(id: str = Query(..., min_length=1, max_length=16),
              tf: str = Query("1m", max_length=4, description="1m | 5m | 15m | 1h"),
              step: Optional[float] = Query(None, description="price step; omit for an automatic one"),
              limit: int = Query(30, description="bars, clamped to 1..120"),
              since: Optional[float] = Query(None, description="only bars starting at or after this epoch s"),
              rows: int = Query(32, description="automatic step: aim for at most this many price rows (8..200)")):
    """Footprint bars from real aggTrades: per bar and price step, the volume sold into the
    bid and bought at the ask (taker side), bar delta, point of control. `partial` marks a
    bar whose period is not completely held (history still loading, or before the tape starts)."""
    inst = universe.require_cap(id, "trades")
    if tf not in TF:
        raise BadRequest("tf must be one of " + ", ".join(TF))
    tf_s = TF[tf]
    limit = max(1, min(int(limit), BARS_MAX))
    rows = max(8, min(int(rows), 200))
    store, now = trade_store(inst["id"]), time.time()
    store.wanted_at = now
    ensure_workers()
    if not store.intervals:                               # nothing yet (no stream, or it has not traded): seed by REST
        _ingest_page(store, universe.vendor(inst["id"], SOURCE), ttl=TRADES_TTL)
    head_ms = store.head_ms(now)
    if head_ms is None:
        raise UpstreamError(SOURCE, None, f"no trades are known yet for {inst['id']}")
    last_t = head_ms // 1000 // tf_s * tf_s
    first_t = last_t - (limit - 1) * tf_s
    if since is not None and math.isfinite(since):
        first_t = max(first_t, int(since) // tf_s * tf_s)
    minutes, intervals, base_ticks, last_ms = store.snapshot(first_t)
    options = _step_options(base_ticks)
    if step is not None:
        step_ticks = _ticks(step) if math.isfinite(step) and step > 0 else 0
        if step_ticks not in options:
            raise BadRequest("step must be one of " + ", ".join("%g" % _px(o) for o in options) + f" for {inst['id']}")
    else:
        his = [v[1] for m, v in minutes.items() if first_t <= m // tf_s * tf_s <= last_t]
        los = [v[2] for m, v in minutes.items() if first_t <= m // tf_s * tf_s <= last_t]
        span = (max(his) - min(los)) if his else 0.0
        step_ticks = options[-1]
        for o in options:
            if span / _px(o) <= rows:
                step_ticks = o
                break
    bars = build_bars(minutes, base_ticks, tf_s, step_ticks, first_t, last_t)
    by_t = {b["t"]: b for b in bars}
    out, started = [], False
    for t in range(first_t, last_t + 1, tf_s):
        a_ms, b_ms = t * 1000, min((t + tf_s) * 1000 - 1, head_ms)
        whole = store.covered(a_ms, b_ms, now)
        bar = by_t.get(t)
        if bar is None:
            if not whole and not started:
                continue                                  # before the tape starts: nothing to show
            bar = {"t": t, "o": None, "h": None, "l": None, "c": None, "n": 0, "buy": 0.0, "sell": 0.0,
                   "vol": 0.0, "delta": 0.0, "poc": None, "cells": []}
        started = True
        bar["partial"] = not whole
        bar["open"] = (t + tf_s) * 1000 - 1 > head_ms
        out.append(bar)
    lo_ms = first_t * 1000
    gaps = []
    ivs = [iv for iv in intervals if iv[1] >= lo_ms]
    for i in range(1, len(ivs)):
        gaps.append([ivs[i - 1][1] / 1000.0, ivs[i][0] / 1000.0])
    return {"id": inst["id"], "source": SOURCE, "freshness": "RT",
            "as_of": last_ms / 1000.0 if last_ms else None, "server_time": now,
            "tf": tf, "tf_s": tf_s, "tick": TICK, "step": _px(step_ticks), "base_step": _px(base_ticks),
            "steps": [_px(o) for o in options], "auto": step is None,
            "bars": out,
            "coverage": {"from": intervals[0][0] / 1000.0 if intervals else None, "to": head_ms / 1000.0,
                         "gaps": gaps, "stream": store.live(now), "backfill": store.backfill,
                         "backfill_target_s": BACKFILL_S, "error": store.error},
            "retention_s": MINUTES_KEPT * 60}
