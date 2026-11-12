"""QuoteService — the one place that knows the price of the 23 instruments
(REBUILD_SPEC §3.3).

* Source chains with fallback (core.universe): the first source in an instrument's
  chain that holds a FRESH answer wins, and the quote records which one it was.
* One poller, whatever the number of browser tabs:
      Yahoo spark batch (18 symbols, one call)            every 15 s
      Alpaca IEX snapshots (4 symbols, one call)          every 5 s while the US session
                                                          is open, every 60 s otherwise
      Binance tradingDay ticker (day open/high/low/vol)   every 15 s (5 s + bookTicker when
                                                          the websocket stream is not ticking)
      Binance 15-minute bars since 00:00 UTC (sparkline)  every 5 min
      Treasury.gov par curve, Bundesbank series (EOD)     every 30 min
  Fallback sources (Tiingo, LSE preview, CBOE delayed quotes, FRED) are only asked for
  instruments whose earlier sources have been TRIED and hold nothing fresh (a source
  whose poll is due or in flight has not been tried yet), and no more often than once
  per 60 / 120 / 60 / 1800 s. Every upstream call goes through core.cache.
* It polls only while somebody asked (REST or WS) in the last 90 s (`touch()`).
* Jobs run on a fixed cadence and independently of one another (a source that hangs
  delays nobody else); the upstream call rate does not depend on the number of clients.
  REST readers get the in-memory state at once; at a cold start or after an idle
  period they wait for the refresh, but never longer than COLD_WAIT seconds — what has
  not answered by then is returned as "none" (note: "still loading") and arrives with
  a later tick / websocket `quote` event.
* A source that answers HTTP 429 is left alone for max(interval, Retry-After), or for
  60 s doubling to 10 min when it sends no Retry-After; the quote note says so.
* `chg` / `chg_pct` are versus the previous session close (crypto: the 00:00 UTC open),
  never versus an arbitrary history bar.
* status: "ok" → "stale" (no successful refresh for more than 3× the poll interval;
  the value is kept and `ts` shows its age) → "none" (nothing usable: `last` is null
  and `note` says why). "none" is reserved for real outages: a value that is old only
  because nobody was watching (the poller idles) stays "stale" until the refresh.
* Live ticks (Binance / Alpaca websocket) enter through `on_tick`; they update the
  same quote. The spot price of BTC/ETH/SOL only ever comes from Binance. The day
  figures (previous close, open, high, low, volume) come from REST: when REST stops
  answering while the stream goes on, they are withheld rather than left frozen.
* A source never moves a quote back in time: an answer whose `ts` is older than the
  one already held (Yahoo serves from caches of different ages) is ignored.

Consumers: `SERVICE.snapshot()` / `SERVICE.quote(id)` (REST; other slices call
`SERVICE.quote(id)` for a current price: it wakes the poller and waits at most
COLD_WAIT), `SERVICE.get(id)` (memory only: no refresh, no upstream call — "stale"
when no client has been watching), `SERVICE.listen(fn)` + `SERVICE.changes(id)`
(the /ws fan-out).
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from core import cache, errors, session, universe
from core.util import epoch_date

log = logging.getLogger("uvicorn.error").getChild("quotes")

ACTIVE_WINDOW = 90.0          # poll only while a client asked within this many seconds
STALE_FACTOR = 3.0            # stale when now - last successful refresh > 3 x interval
FALLBACK_FACTOR = 2.0         # ask the next source in the chain once a value is older than 2 x interval
                              # (two missed polls), so it is ready before the value turns stale
NONE_FACTOR, NONE_MIN = 20.0, 300.0   # give the value up ("none") after max(20 x interval, 300 s)
STREAM_FRESH = 5.0            # a websocket tick younger than this makes REST last/bid/ask redundant
SPARK_MAX = 120
TICK = 0.25                   # poller wake-up period (jobs run only when due)

# poll interval per source (seconds); alpaca / binance depend on session / stream state
INTERVAL = {"yahoo": 15.0, "alpaca": 5.0, "alpaca_closed": 60.0, "binance": 15.0,
            "binance_no_stream": 5.0, "binance_spark": 300.0, "treasury": 1800.0, "bundesbank": 1800.0,
            "tiingo": 60.0, "lse": 120.0, "cboe": 60.0, "fred": 1800.0}
# how long a value from each source counts as fresh = STALE_FACTOR x this
FRESH_INTERVAL = {"yahoo": 15.0, "alpaca": 5.0, "binance": 5.0, "treasury": 1800.0,
                  "bundesbank": 1800.0, "tiingo": 60.0, "lse": 120.0, "cboe": 60.0, "fred": 1800.0}
PRIMARY_JOBS = ("yahoo", "alpaca", "binance", "binance_spark", "treasury", "bundesbank")
WAIT_SECONDS = 25.0           # longest an internal fan-out (crypto sparklines) waits for its workers
COLD_WAIT = 3.0               # longest a REST caller waits for a refresh (cold start / after idling)
FALLBACK_WAIT = 1.5           # ...plus this much for the fallback sources that turned out to be needed
BACKOFF_MIN, BACKOFF_MAX = 60.0, 600.0   # after HTTP 429 without Retry-After: 60 s doubling to 10 min
RETRY_AFTER_MAX = 3600.0      # never trust a Retry-After beyond an hour
DAY_FRESH = 45.0              # stream-fed quotes: REST day figures older than this lose their volume
YAHOO_REGRESS = 900.0         # a Yahoo answer up to this much OLDER than the one held is ignored
PUBLIC_FIELDS = ("last", "prev_close", "chg", "chg_pct", "bid", "ask", "open", "high", "low",
                 "volume", "ts", "source", "freshness", "session", "status", "note", "as_of", "spark")


def _thin(points: list) -> list:
    """At most SPARK_MAX points: every k-th close plus the last one (real values only)."""
    n = len(points)
    if n <= SPARK_MAX:
        return points
    k = math.ceil(n / (SPARK_MAX - 1))
    out = points[::k]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def _hms(t: float) -> str:
    return time.strftime("%H:%M:%SZ", time.gmtime(t))


class QuoteService:
    def __init__(self):
        self._lock = threading.RLock()
        self._cand: dict[tuple[str, str], dict] = {}      # (source, id) -> candidate quote
        self._errors: dict[str, dict] = {}                # source -> {"message", "at"}
        # at = last start, done_at = last finish (ok or not), ok_at = last success
        self._jobs = {name: {"at": 0.0, "next": 0.0, "ok_at": 0.0, "done_at": 0.0,
                             "lock": threading.Lock()} for name in PRIMARY_JOBS}
        self._asked = 0.0
        self._sent: dict[str, dict] = {}                  # last quote handed to the fan-out
        self._listeners: list = []
        self._pool: ThreadPoolExecutor | None = None
        self._fb_busy: set = set()                        # fallback fetches in flight
        self._fb_next: dict[str, float] = {}              # fallback key -> earliest next attempt
        self._limited: dict[str, float] = {}              # job / fallback key -> current 429 back-off (s)
        self._logged: dict[str, float] = {}
        self._running = False                             # the background poller task is alive
        self.enabled = True                               # False: no background polling (OT_QUOTES=0)

    # ------------------------------------------------------------ activity
    def touch(self) -> None:
        """A client asked for quotes: keep the poller running for ACTIVE_WINDOW seconds."""
        self._asked = time.time()

    def active(self) -> bool:
        return time.time() - self._asked < ACTIVE_WINDOW

    def listen(self, fn) -> None:
        """fn(id) is called (from any thread) whenever a quote may have changed."""
        self._listeners.append(fn)

    def _notify(self, id_: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(id_)
            except Exception:                 # a listener must never break the poller
                log.exception("quote listener failed")

    # ------------------------------------------------------------- polling
    def _interval(self, job: str, now: float) -> float:
        if job == "alpaca":
            # 5 s only while the session is KNOWN to be open ("unknown" polls like closed)
            return INTERVAL["alpaca"] if session.state("us_equity", now) == session.OPEN \
                else INTERVAL["alpaca_closed"]
        if job == "binance":
            return INTERVAL["binance"] if self._stream_fresh(now) else INTERVAL["binance_no_stream"]
        return INTERVAL[job]

    def _due(self, job: str, now: float) -> bool:
        return now >= self._jobs[job]["next"]

    def _stream_fresh(self, now: float) -> bool:
        with self._lock:
            return all(now - (self._cand.get(("binance", i)) or {}).get("stream_at", 0.0) < STREAM_FRESH
                       for i in universe.ids("binance"))

    def _executor(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="quotes")
        return self._pool

    def _wait(self, futures: list, deadline: float) -> None:
        """Wait for worker runs until `deadline` (time.monotonic()). A run that is not
        finished by then simply carries on; its result arrives with a later tick."""
        for fut in futures:
            try:
                fut.result(timeout=max(0.0, deadline - time.monotonic()))
            except (FutureTimeout, TimeoutError):
                pass
            except Exception:
                log.exception("quote job crashed")

    def _backoff(self, key: str, exc: BaseException | None, interval: float) -> float:
        """Seconds to leave a source alone after a failure: 0 unless it said "slow down"
        (HTTP 429). With a Retry-After header: max(interval, Retry-After); without one:
        60 s, doubling per consecutive 429 up to 10 min. A success resets it."""
        if not isinstance(exc, errors.RateLimited):
            self._limited.pop(key, None)
            return 0.0
        if exc.retry_after is not None:
            self._limited.pop(key, None)
            return max(interval, min(float(exc.retry_after), RETRY_AFTER_MAX))
        step = min(BACKOFF_MAX, max(BACKOFF_MIN, 2 * self._limited.get(key, 0.0)))
        self._limited[key] = step
        return max(interval, step)

    def _run_job(self, name: str) -> None:
        job = self._jobs[name]
        with job["lock"]:                               # one run at a time; latecomers re-check
            now = time.time()
            if not self._due(name, now):
                return
            # Fixed cadence: the next run is one interval after the previous DUE time, not
            # after whoever happened to trigger this run — so the upstream call rate is
            # the same with one client or fifty. (After idling, start a new cadence.)
            interval = self._interval(name, now)
            job["next"] = job["next"] + interval if job["next"] + interval > now else now + interval
            job["at"] = now
            try:
                getattr(self, f"_job_{name}")()
                job["ok_at"] = time.time()
                self._errors.pop(name, None)
                self._backoff(name, None, interval)
            except Exception as e:
                wait = self._backoff(name, e, interval)
                if wait:                                # HTTP 429: stay away, whatever the cadence says
                    job["next"] = max(job["next"], time.time() + wait)
                self._fail(name, e, retry_at=time.time() + wait if wait else None)
            finally:
                job["done_at"] = time.time()

    def _fail(self, source: str, exc: BaseException, retry_at: float | None = None) -> None:
        info = errors.describe(exc)
        if not isinstance(exc, errors.ApiError):
            # a reply of an unexpected shape (parser tripped): a failure of the source
            info["message"] = f"unusable reply ({info['message'][:120]})"
        self._errors[source] = {"message": info["message"], "code": info["code"], "at": time.time(),
                                "retry_at": retry_at}
        if not isinstance(exc, errors.ApiError) and time.time() - self._logged.get(source, 0.0) > 60:
            self._logged[source] = time.time()
            log.error("quote source %s failed unexpectedly", source, exc_info=exc)

    def refresh(self, wait: bool = True) -> None:
        """Start every poll job that is due and the fallbacks that are needed, then tell
        listeners what changed. Jobs run in a thread pool, independently of one another.

        wait=True  (REST at a cold start, tests): block until those runs have finished, but
                   at most COLD_WAIT (+ FALLBACK_WAIT) seconds: one slow source must not
                   hold back the answers of all the others.
        wait=False (the poller's tick): return at once; results arrive on later ticks."""
        now = time.time()
        deadline = time.monotonic() + COLD_WAIT
        futures = [self._executor().submit(self._run_job, name) for name in PRIMARY_JOBS
                   if self._due(name, now) and not self._jobs[name]["lock"].locked()]
        if wait:
            self._wait(futures, deadline)
            for name in PRIMARY_JOBS:                   # a run somebody else started
                job = self._jobs[name]
                if job["done_at"] < job["at"] and time.time() - job["at"] > COLD_WAIT:
                    continue                            # in flight for long already: do not queue behind it
                if job["lock"].acquire(timeout=max(0.0, deadline - time.monotonic())):
                    job["lock"].release()
        futures = [self._executor().submit(self._run_fallback, key, source, fn)
                   for key, source, fn in self._fallback_calls(time.time())]
        if wait and futures:
            self._wait(futures, max(deadline, time.monotonic()) + FALLBACK_WAIT)
        self.sweep()

    def sweep(self) -> None:
        """Notify listeners about every quote that differs from what was last sent
        (also catches pure time transitions: ok → stale → none, session open/closed)."""
        if not self._listeners:
            return
        now = time.time()
        for id_ in universe.ORDER:
            cur, sent = self._public(id_, now), self._sent.get(id_)
            if sent is None or any(cur[k] != sent.get(k) for k in PUBLIC_FIELDS if k != "as_of"):
                self._notify(id_)

    async def run(self) -> None:
        """The background poller (one per process). Idles when nobody asked recently."""
        self._running = True
        try:
            while True:
                try:
                    if self.enabled and self.active():
                        self.refresh(wait=False)        # non-blocking: jobs run in the pool
                    else:
                        self.sweep()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("quote poller cycle failed")
                await asyncio.sleep(TICK)
        finally:
            self._running = False

    def stop(self) -> None:
        """Release the worker threads (shutdown). The service can be used again afterwards."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _must_wait(self, now: float) -> bool:
        """True at a cold start, after idling, or when no poller runs: the caller should
        wait for a refresh instead of being handed old data."""
        if not (self._running and self.enabled):
            return True
        # judged by when a job last FINISHED: a first run that is still in flight (the poller
        # started it a moment ago for a websocket client) has produced nothing yet
        return not all(self._on_cadence(name, now) for name in PRIMARY_JOBS)

    def _on_cadence(self, name: str, now: float) -> bool:
        """The job has finished a run recently (it is being polled, not idling), or it is
        deliberately held back after an HTTP 429."""
        job = self._jobs[name]
        if job["done_at"] == 0.0:
            return False
        if now - job["done_at"] <= 2 * self._interval(name, now) + 5:
            return True
        return ((self._errors.get(name) or {}).get("retry_at") or 0.0) > now

    # ------------------------------------------------------------ candidates
    def _put(self, source: str, id_: str, fields: dict, at: float,
             interval: float | None = None) -> None:
        """Store what a source said. `interval` is how often that source is being polled
        right now (it decides when the value counts as stale)."""
        with self._lock:
            c = self._cand.setdefault((source, id_), {})
            held, new = c.get("ts"), fields.get("ts")
            if source == "yahoo" and held and new and 0 < held - new <= YAHOO_REGRESS:
                # Yahoo answered from an older cache than last time (seen live: FX `ts`
                # stepping back by minutes). The source is alive (`at` moves on) but the
                # quote must not go back in time. A larger step back is something else
                # (contract roll, new session) and is taken as it comes.
                fields = {}
            c.update(fields)
            c["at"] = max(at, c.get("at", 0.0))
            if at > 0.0:
                c["day_at"] = max(at, c.get("day_at", 0.0))     # last REST answer (ticks do not move it)
            c["interval"] = interval or FRESH_INTERVAL[source]

    def _fresh(self, source: str, id_: str, now: float) -> bool:
        c = self._cand.get((source, id_))
        return bool(c) and c.get("last") is not None \
            and now - c["at"] <= STALE_FACTOR * c["interval"]

    # --------------------------------------------------------- primary jobs
    def _job_yahoo(self) -> None:
        from providers import yahoo
        symbols = {universe.vendor(i, "yahoo"): i for i in universe.ids("yahoo")}
        got = cache.fetch("yahoo:spark", INTERVAL["yahoo"] - 2,
                          lambda: yahoo.parse_spark(yahoo.spark(list(symbols))), stale_ttl=0)
        for sym, q in got.value.items():
            id_ = symbols.get(sym)
            if not id_:
                continue
            fields = {k: q.get(k) for k in ("last", "prev_close", "ts", "high", "low", "volume",
                                            "period", "name")}
            fields["open"] = fields["bid"] = fields["ask"] = None
            fields["spark"] = q.get("spark") or []
            # Before a session's first print Yahoo reports previous close == last with no
            # intraday bars: the day change is then unknown, not zero.
            fields["prev_unreliable"] = (not fields["spark"]
                                         and fields["prev_close"] == fields["last"])
            self._put("yahoo", id_, fields, got.at)
        missing = [i for s, i in symbols.items() if s not in got.value]
        if missing:
            self._errors["yahoo:partial"] = {"message": f"no price for {', '.join(missing)}",
                                             "code": "upstream_error", "at": time.time(), "ids": missing}
        else:
            self._errors.pop("yahoo:partial", None)

    def _job_alpaca(self) -> None:
        """IEX snapshot (real time, one venue) for the live session; the 15-minute
        delayed consolidated snapshot for the official close and previous close."""
        from providers import alpaca
        ids = universe.ids("alpaca")
        syms = [universe.vendor(i, "alpaca") for i in ids]
        raw = cache.fetch("alpaca:snapshots", 3.0, lambda: alpaca.snapshots(syms, timeout=5),
                          stale_ttl=0)
        try:
            sip_raw = cache.fetch("alpaca:snapshots:delayed_sip", 55.0,
                                  lambda: alpaca.snapshots(syms, feed="delayed_sip", timeout=5),
                                  stale_ttl=0)
            sip = alpaca.parse_snapshots(sip_raw.value, False, session.us_close_ts)
        except errors.RateLimited:
            raise                           # same account: back off the whole job
        except errors.ApiError:
            sip = {}
        # Only a session that is KNOWN to be open publishes the latest trade as the price.
        # "unknown" (no calendar) is treated like closed: the regular-session close of the
        # daily bar — an after-hours print must never pass for a session price.
        is_open = session.state("us_equity", raw.at) == session.OPEN
        iex = alpaca.parse_snapshots(raw.value, is_open, session.us_close_ts)
        today = session.local_date(session.NY, raw.at)
        for id_ in ids:
            sym = universe.vendor(id_, "alpaca")
            q, c = iex.get(sym), sip.get(sym)
            if not q:
                continue
            q = dict(q, tape="iex")
            if is_open and today and q.get("day") and q["day"] < today:
                # the session is open but IEX has not printed today yet: its daily bar is
                # still the previous session's, whose close is the previous close
                prev = c["last"] if (c and c.get("day") == q["day"] and c.get("last")) else q.get("day_close")
                q.update(prev_close=prev, open=None, high=None, low=None, volume=None)
            elif c and c.get("day") and q.get("day"):
                if is_open:
                    # official previous close: the consolidated bar of the day before today's
                    if c["day"] == q["day"] and c.get("prev_close"):
                        q["prev_close"] = c["prev_close"]
                    elif c["day"] < q["day"] and c.get("last"):
                        q["prev_close"] = c["last"]
                elif c["day"] >= q["day"] and c.get("ts") and raw.at - c["ts"] > 20 * 60:
                    q = dict(c, tape="sip")          # the complete consolidated session
            self._put("alpaca", id_, q, raw.at,
                      INTERVAL["alpaca"] if is_open else INTERVAL["alpaca_closed"])

    def _job_binance(self) -> None:
        from providers import binance
        symbols = {universe.vendor(i, "binance"): i for i in universe.ids("binance")}
        day = cache.fetch("binance:tradingDay", 3.0,
                          lambda: binance.trading_day(list(symbols), timeout=4), stale_ttl=0)
        now = time.time()
        book = None
        if not self._stream_fresh(now):
            try:
                book = cache.fetch("binance:bookTicker", 3.0,
                                   lambda: binance.book_ticker(list(symbols), timeout=4), stale_ttl=0)
            except errors.RateLimited:
                raise                       # Binance escalates repeated calls after a 429 to an IP ban
            except errors.ApiError:
                book = None
        for sym, id_ in symbols.items():
            d = day.value.get(sym)
            if not d:
                continue
            fields = {"prev_close": d["open"], "open": d["open"], "volume": d["volume"]}
            with self._lock:
                c = self._cand.get(("binance", id_)) or {}
                stream_live = now - c.get("stream_at", 0.0) < STREAM_FRESH
                # the stream may have seen a print the REST snapshot has not (and vice versa)
                fields["high"] = max(d["high"], c["last"]) if stream_live and c.get("last") else d["high"]
                fields["low"] = min(d["low"], c["last"]) if stream_live and c.get("last") else d["low"]
                # The stream's first frames after a (re)start are depth updates: it is "live"
                # before it has seen a single trade. Until it has, REST supplies the last price.
                if not stream_live or c.get("last") is None:
                    fields.update(last=d["last"], ts=day.at)
                if not stream_live:
                    b = (book.value.get(sym) if book else None) or {}
                    fields.update(bid=b.get("bid"), ask=b.get("ask"))
            self._put("binance", id_, fields, day.at)

    def _job_binance_spark(self) -> None:
        """Today's 15-minute closes (since 00:00 UTC) for the crypto sparklines."""
        from providers import binance
        midnight_ms = int(time.time() // 86400) * 86400 * 1000

        def one(id_):
            sym = universe.vendor(id_, "binance")
            kl = cache.fetch(f"binance:spark:{sym}", INTERVAL["binance_spark"] - 10,
                             lambda: binance.candles(sym, "15m", 100, start_ms=midnight_ms),
                             stale_ttl=3600)
            self._put("binance", id_, {"spark": [[k["t"], k["c"]] for k in kl.value
                                                 if k["t"] * 1000 >= midnight_ms]}, 0.0)
            if kl.stale and (kl.error or {}).get("code") == "rate_limited":
                raise errors.RateLimited("binance", kl.error.get("message") or "rate limited",
                                         retry_after=kl.error.get("retry_after"))
        failure = None
        for fut in [self._executor().submit(one, i) for i in universe.ids("binance")]:
            try:
                fut.result(timeout=WAIT_SECONDS)
            except Exception as e:
                failure = e
        if failure is not None:
            raise failure

    def _eod(self, source: str, id_: str, points: list, at: float) -> None:
        if not points:
            raise errors.UpstreamError(source, None, "no observations")
        last, prev = points[-1], (points[-2] if len(points) > 1 else None)
        self._put(source, id_, {
            "last": last["value"], "prev_close": prev["value"] if prev else None,
            "ts": last["t"], "date": last["date"], "bid": None, "ask": None, "open": None,
            "high": None, "low": None, "volume": None, "spark": []}, at)

    def _job_treasury(self) -> None:
        from providers import treasury
        year = time.gmtime().tm_year

        def load():
            rows = []
            try:
                rows = treasury.parse_curve(treasury.par_curve(year))
            except errors.UpstreamError:
                if time.gmtime().tm_yday > 10:
                    raise
            if len(rows) < 2:               # first days of January: also read last year
                rows = treasury.parse_curve(treasury.par_curve(year - 1)) + rows
            return rows
        got = cache.fetch("treasury:curve:quote", INTERVAL["treasury"] - 5, load, stale_ttl=0)
        for id_ in universe.ids("treasury"):
            self._eod("treasury", id_, treasury.series(got.value, universe.vendor(id_, "treasury")), got.at)

    def _job_bundesbank(self) -> None:
        from providers import bundesbank
        got = cache.fetch("bundesbank:10y:quote", INTERVAL["bundesbank"] - 5,
                          lambda: bundesbank.parse_csv(bundesbank.series_csv(last_n=12)), stale_ttl=0)
        for id_ in universe.ids("bundesbank"):
            self._eod("bundesbank", id_, got.value, got.at)

    # ------------------------------------------------------------ fallbacks
    def _settled(self, source: str, now: float) -> bool:
        """A polled source has been tried and its answer is in: it ran at least once, is
        not being fetched right now and is not due for a run. (The poller's tick submits
        the due jobs and evaluates the fallbacks in the same instant: without the `due`
        test every value that aged during an idle period, or over one failed poll, looked
        abandoned and woke the whole fallback chain although the primary was healthy.)
        A source held back after an HTTP 429 counts as tried."""
        job = self._jobs.get(source)
        if job is None:
            return True
        return job["at"] > 0.0 and not job["lock"].locked() and not self._due(source, now)

    def _needs(self, id_: str, source: str, now: float) -> bool:
        """True when `source` is in the chain and every source before it has been tried
        and holds nothing recent (missed two polls in a row, or never answered)."""
        for s, _ in universe.INSTRUMENTS[id_]["chain"]:
            if s == source:
                return True
            if not self._settled(s, now):
                return False
            c = self._cand.get((s, id_))
            if c and c.get("last") is not None and now - c["at"] <= FALLBACK_FACTOR * c["interval"] \
                    and not (c.get("prev_unreliable") and source == "cboe"):
                return False
        return False

    def _fallback_calls(self, now: float) -> list:
        """[(key, source, fn)] for the fallback fetches that are needed and allowed now
        (each key at most once per its source's interval, successful or not)."""
        wanted = []
        eq = [i for i in universe.ids("tiingo", "equity") if self._needs(i, "tiingo", now)]
        fx = [i for i in universe.ids("tiingo", "fx") if self._needs(i, "tiingo", now)]
        if eq:
            wanted.append(("tiingo:iex", "tiingo", lambda: self._fb_tiingo("iex", eq)))
        if fx:
            wanted.append(("tiingo:fx", "tiingo", lambda: self._fb_tiingo("fx", fx)))
        for src, fn in (("lse", self._fb_lse), ("cboe", self._fb_cboe), ("fred", self._fb_fred)):
            for id_ in universe.ids(src):
                if self._needs(id_, src, now):
                    wanted.append((f"{src}:{id_}", src, lambda f=fn, i=id_: f(i)))
        calls = []
        with self._lock:
            for key, source, fn in wanted:
                if key in self._fb_busy or now < self._fb_next.get(key, 0.0):
                    continue
                self._fb_busy.add(key)
                self._fb_next[key] = now + INTERVAL[source]
                calls.append((key, source, fn))
        return calls

    def _run_fallback(self, key: str, source: str, fn) -> None:
        try:
            fn()
            self._errors.pop(source, None)
            self._backoff(key, None, INTERVAL[source])
        except Exception as e:
            wait = self._backoff(key, e, INTERVAL[source])
            if wait:
                with self._lock:
                    self._fb_next[key] = max(self._fb_next.get(key, 0.0), time.time() + wait)
            self._fail(source, e, retry_at=time.time() + wait if wait else None)
        finally:
            self._fb_busy.discard(key)

    def _fb_tiingo(self, kind: str, ids: list) -> None:
        from providers import tiingo
        if kind == "iex":
            all_ids = universe.ids("tiingo", "equity")
            got = cache.fetch("tiingo:iex", INTERVAL["tiingo"] - 2, lambda: tiingo.parse_iex(
                tiingo.iex([universe.vendor(i, "tiingo") for i in all_ids])), stale_ttl=0)
        else:
            all_ids = universe.ids("tiingo", "fx")
            got = cache.fetch("tiingo:fx", INTERVAL["tiingo"] - 2, lambda: tiingo.parse_fx_top(
                tiingo.fx_top([universe.vendor(i, "tiingo") for i in all_ids])), stale_ttl=0)
        for id_ in all_ids:
            q = got.value.get(universe.vendor(id_, "tiingo").upper())
            if not q:
                continue
            fields = {"open": None, "high": None, "low": None, "volume": None, "prev_close": None,
                      "prev_from": None, "spark": [], **q}
            if kind == "fx":
                # no previous close in Tiingo's top of book: reuse the one Yahoo gave for
                # the same UTC day, otherwise the change stays unknown
                y = self._cand.get(("yahoo", id_))
                if y and y.get("prev_close") and epoch_date(y["at"]) == epoch_date(got.at):
                    fields["prev_close"] = y["prev_close"]
                    fields["prev_from"] = "yahoo"
            self._put("tiingo", id_, fields, got.at)

    def _fb_lse(self, id_: str) -> None:
        from providers import lse
        market, symbol = universe.vendor(id_, "lse")

        def load():
            minute = lse.parse_preview(lse.preview(market, symbol, "1m"))
            daily = lse.parse_preview(lse.preview(market, symbol, "1d"))
            q = lse.quote_from_previews(minute, daily)
            if not q:
                raise errors.UpstreamError("lse", None, f"preview returned no bars for {symbol}")
            return q
        got = cache.fetch(f"lse:quote:{id_}", INTERVAL["lse"] - 2, load, stale_ttl=0)
        self._put("lse", id_, {"bid": None, "ask": None, "spark": [], **got.value}, got.at)

    def _fb_cboe(self, id_: str) -> None:
        from providers import cboe_quotes
        sym = universe.vendor(id_, "cboe")

        def load():
            q = cboe_quotes.parse_quote(cboe_quotes.quote(sym),
                                        lambda s: session.local_to_epoch(s, session.NY))
            if not q:
                raise errors.UpstreamError("cboe", None, f"no price for {sym}")
            return q
        got = cache.fetch(f"cboe:quote:{id_}", INTERVAL["cboe"] - 2, load, stale_ttl=0)
        self._put("cboe", id_, {"spark": [], **got.value}, got.at)

    def _fb_fred(self, id_: str) -> None:
        from providers import treasury
        series = universe.vendor(id_, "fred")
        got = cache.fetch(f"fred:{series}", INTERVAL["fred"] - 5,
                          lambda: treasury.parse_fred(treasury.fred_series(series, 6)), stale_ttl=0)
        self._eod("fred", id_, got.value, got.at)

    # ------------------------------------------------------------ live ticks
    def on_tick(self, source: str, id_: str, last=None, ts=None, bid=None, ask=None) -> None:
        """A websocket trade (last, ts) and/or top-of-book update (bid, ask)."""
        now = time.time()
        with self._lock:
            c = self._cand.setdefault((source, id_), {"interval": FRESH_INTERVAL[source]})
            if last is not None:
                c["last"] = last
                c["ts"] = ts if ts is not None else now
                if c.get("high") is not None and last > c["high"]:
                    c["high"] = last
                if c.get("low") is not None and last < c["low"]:
                    c["low"] = last
            if bid is not None:
                c["bid"] = bid
            if ask is not None:
                c["ask"] = ask
            c["at"] = c["stream_at"] = now
        self._notify(id_)

    # --------------------------------------------------------------- output
    def _resolve(self, id_: str, now: float):
        """(source, candidate) — first fresh source in the chain, else the newest answer."""
        newest = None
        for source, _ in universe.INSTRUMENTS[id_]["chain"]:
            c = self._cand.get((source, id_))
            if not c or c.get("last") is None:
                continue
            if now - c["at"] <= STALE_FACTOR * c["interval"]:
                return source, c
            if newest is None or c["at"] > newest[1]["at"]:
                newest = (source, c)
        return newest or (None, None)

    def _why(self, id_: str) -> str:
        parts = []
        partial = self._errors.get("yahoo:partial")
        for source, _ in universe.INSTRUMENTS[id_]["chain"]:
            err, job = self._errors.get(source), self._jobs.get(source)
            if err:
                parts.append(f"{source}: {self._err_text(err)}")
            elif source == "yahoo" and partial and id_ in (partial.get("ids") or ()):
                parts.append("yahoo: answered without a price for this symbol")
            elif job is not None and (job["done_at"] < job["at"] or job["lock"].locked()):
                parts.append(f"{source}: still loading")
            else:
                parts.append(f"{source}: no answer yet")
        return "; ".join(parts)

    @staticmethod
    def _err_text(err: dict, now: float | None = None) -> str:
        """An error message plus, after an HTTP 429, when the source is asked again."""
        now = time.time() if now is None else now
        retry_at = err.get("retry_at")
        if retry_at and retry_at > now:
            return f"{err['message']} — rate limited, next try in {int(retry_at - now) + 1} s"
        return err["message"]

    def _attempted_since(self, id_: str, at: float) -> float:
        """Seconds between a value fetched at `at` and the last FINISHED attempt to refresh
        the instrument's chain (0 when nothing was tried since: the poller was idle)."""
        job = self._jobs.get(universe.INSTRUMENTS[id_]["chain"][0][0])
        return max(0.0, job["done_at"] - at) if job else float("inf")

    def _day_figures(self, source: str, c: dict, now: float):
        """For a quote whose price comes from a websocket stream: are the REST-derived day
        figures still usable? -> ("ok" | "aged" | "void", since). "void": they belong to
        another day/session (previous close, open, high, low, volume and the change must
        go); "aged": the same day, but REST has been silent (volume is frozen)."""
        day_at, stream_at = c.get("day_at") or 0.0, c.get("stream_at") or 0.0
        if source not in ("binance", "alpaca") or stream_at <= day_at:
            return "ok", day_at
        if not day_at:
            return "void", None
        if source == "binance" and epoch_date(day_at) != epoch_date(now):
            return "void", day_at                       # the 00:00 UTC open of today is not known
        if source == "alpaca" and (not c.get("regular")
                                   or session.local_date(session.NY, day_at) != session.local_date(session.NY, now)):
            return "void", day_at                       # snapshot from before this session opened
        return ("aged" if now - day_at > DAY_FRESH else "ok"), day_at

    def _public(self, id_: str, now: float) -> dict:
        inst = universe.INSTRUMENTS[id_]
        with self._lock:
            source, c = self._resolve(id_, now)
            c = dict(c) if c else None
        calendar = inst["calendar"]
        # "none" needs a real outage: the value is too old AND a refresh was attempted that
        # long after it. A value that aged only because nobody was watching stays (stale).
        give_up = max(NONE_FACTOR * c["interval"], NONE_MIN) if c else 0.0
        if c is None or (now - c["at"] > give_up and self._attempted_since(id_, c["at"]) > give_up):
            if c is None:
                why = f"no source answered — {self._why(id_)}"
            else:
                why = (f"no source has answered since {_hms(c['at'])} — {self._why(id_)}")
            return {"id": id_, "last": None, "prev_close": None, "chg": None, "chg_pct": None,
                    "bid": None, "ask": None, "open": None, "high": None, "low": None,
                    "volume": None, "ts": None, "source": None,
                    "freshness": inst["chain"][0][1], "session": session.state(calendar, now),
                    "status": "none", "note": why, "as_of": c["at"] if c else None, "spark": []}
        nd = inst["decimals"] + 2
        last, prev = c["last"], c.get("prev_close")
        notes = []
        if c.get("prev_unreliable") and source == "yahoo":
            alt = self._cand.get(("cboe", id_))
            if alt and alt.get("prev_close") and abs(alt["last"] - last) <= 0.005 * abs(last):
                prev = alt["prev_close"]
                notes.append("previous close from CBOE delayed quotes")
            else:
                prev = None
                notes.append("day change not available before the session's first print")
        high, low, volume = c.get("high") or None, c.get("low") or None, c.get("volume") or None
        if high is not None and low is not None and not (low <= last <= high):
            # a day range must contain the last print; forgive a one-tick race, otherwise
            # the source's range is not usable (seen on Yahoo RB=F around contract rolls)
            if low - last <= 0.001 * abs(last) and last - high <= 0.001 * abs(last):
                high, low = max(high, last), min(low, last)
            else:
                high = low = None
                notes.append(f"day range withheld: {source} reports a range that excludes the last price")
        opened = c.get("open")
        day_state, day_since = self._day_figures(source, c, now)
        if day_state != "ok":
            err = self._errors.get(source)
            since = f"since {_hms(day_since)}" if day_since else "yet"
            why = f"{source} REST has not answered {since}" + (f" ({self._err_text(err, now)})" if err else "")
            volume = None
            if day_state == "void":
                prev = opened = high = low = None
                notes.append(f"day change and range withheld — {why}; the price is live from the stream")
            else:
                notes.append(f"volume withheld — {why}; the price is live from the stream")
        chg = round(last - prev, nd) if prev else None
        chg_pct = round((last - prev) / prev * 100, 2) if prev else None
        freshness = universe.freshness(id_, source)
        if source == "lse":
            freshness = "D15" if c.get("ts") and now - c["ts"] <= 1200 else "EOD"
        if source == "alpaca" and c.get("tape") == "sip":
            freshness = "D15"
        stale = now - c["at"] > STALE_FACTOR * c["interval"]
        period = c.get("period") if source == "yahoo" and inst["cls"] in ("index", "rate") else None
        # ---- notes: where the number comes from and what it is measured against
        cls = inst["cls"]
        if cls == "crypto" and prev:
            notes.append("change vs 00:00 UTC open (Binance spot)")
        if cls == "future" and source == "yahoo" and c.get("name"):
            notes.append(f"front contract: {c['name']}")
        if source == "alpaca":
            if c.get("regular"):
                notes.append("IEX venue only (volume is IEX volume)")
            else:
                notes.append("regular-session close (consolidated tape)" if c.get("tape") == "sip"
                             else "regular-session close (IEX venue)")
        if source == "lse":
            notes.append("LSE preview: last 1-minute bar")
        if source == "tiingo" and cls == "fx":
            notes.append("mid of Tiingo top of book" + ("; previous close from Yahoo"
                                                        if c.get("prev_from") else
                                                        "; previous close not available"))
        if c.get("date"):
            label = {"treasury": "Treasury.gov par yield curve", "fred": "FRED",
                     "bundesbank": "Bundesbank fitted yield"}.get(source, source)
            notes.append(f"end of day {c['date']} ({label})")
        first = inst["chain"][0][0]
        if source != first:
            err = self._errors.get(first)
            notes.append(f"fallback — {first}: {self._err_text(err, now) if err else 'no fresh answer'}")
        if cls == "fx" and source == "yahoo":
            notes.append("Yahoo FX: indicative, about a minute behind the market")
        if stale:
            err = self._errors.get(source)
            if not err and self._attempted_since(id_, c["at"]) <= FALLBACK_FACTOR * c["interval"]:
                notes.append(f"last refreshed {_hms(c['at'])} — the quote poller was idle "
                             "(no client was watching); refreshing")
            else:
                notes.append(f"{source} has not answered since {_hms(c['at'])}"
                             + (f" ({self._err_text(err, now)})" if err else ""))
        spark = c.get("spark") or []
        if not spark and source != "yahoo" and cls in ("equity", "fx", "future", "index", "rate"):
            y = self._cand.get(("yahoo", id_))          # intraday shape of the same session
            if y and y.get("spark") and now - y["at"] <= STALE_FACTOR * y["interval"]:
                spark = y["spark"]
        return {"id": id_, "last": last, "prev_close": prev, "chg": chg, "chg_pct": chg_pct,
                "bid": c.get("bid"), "ask": c.get("ask"), "open": opened,
                "high": high, "low": low, "volume": volume,
                "ts": c.get("ts"), "source": source, "freshness": freshness,
                "session": session.state(calendar, now, period),
                "status": "stale" if stale else "ok",
                "note": " · ".join(notes) if notes else None,
                "as_of": c["at"], "spark": _thin(spark)}

    def get(self, id_: str) -> dict:
        """The current quote of one instrument, from memory (no refresh, no upstream call)."""
        return self._public(universe.require(id_)["id"], time.time())

    def quote(self, id_: str) -> dict:
        """REST: one quote (waits for a refresh only at a cold start / after idling)."""
        inst = universe.require(id_)
        self.touch()
        if self._must_wait(time.time()):
            self.refresh(wait=True)
        return self._public(inst["id"], time.time())

    def snapshot(self) -> dict:
        """REST: all 23 quotes (waits for a refresh only at a cold start / after idling)."""
        self.touch()
        if self._must_wait(time.time()):
            self.refresh(wait=True)
        now = time.time()
        return {"as_of": now, "source": "mixed",
                "quotes": {i: self._public(i, now) for i in universe.ORDER}}

    def changes(self, id_: str) -> dict | None:
        """The `quote` websocket event for an instrument: id, last, bid, ask, ts, source
        plus every field that changed since the previous call; None if nothing did."""
        cur = self._public(id_, time.time())
        with self._lock:
            sent = self._sent.get(id_)
            diff = {k: cur[k] for k in PUBLIC_FIELDS
                    if sent is None or cur[k] != sent.get(k)}
            if sent is not None and not (set(diff) - {"as_of"}):
                return None
            self._sent[id_] = cur
        event = {"type": "quote", "id": id_}
        for k in ("last", "bid", "ask", "ts", "source"):
            event[k] = cur[k]
        event.update(diff)
        return event

    def health(self) -> dict:
        now = time.time()
        counts = {"ok": 0, "stale": 0, "none": 0}
        newest = 0.0
        for id_ in universe.ORDER:
            q = self._public(id_, now)
            counts[q["status"]] = counts.get(q["status"], 0) + 1
            if q["status"] == "ok" and q["as_of"]:
                newest = max(newest, q["as_of"])
        return {"ok": counts["ok"] > 0, "age": round(now - newest, 1) if newest else None,
                "polling": bool(self.enabled and self.active()), "counts": counts,
                "errors": {s: self._err_text(e, now) for s, e in sorted(self._current_errors(now).items())}}

    def _current_errors(self, now: float) -> dict:
        """Errors that still describe the present. A polled source's error lasts until its
        next success. A fallback source is only asked while an instrument needs it, so its
        last error would otherwise be reported for ever after the outage is over: it is
        reported only while some instrument is not served fresh by an earlier source."""
        out = {}
        for source, e in list(self._errors.items()):
            base = source.split(":")[0]
            if base in self._jobs or (e.get("retry_at") or 0.0) > now or self._wanted(base, now):
                out[source] = e
        return out

    def _wanted(self, source: str, now: float) -> bool:
        """Some instrument currently depends on `source` (or on a later one in its chain)."""
        for id_ in universe.ids(source):
            chain = [s for s, _ in universe.INSTRUMENTS[id_]["chain"]]
            with self._lock:
                got, c = self._resolve(id_, now)
            if got is None or chain.index(got) >= chain.index(source) \
                    or now - c["at"] > STALE_FACTOR * c["interval"]:
                return True
        return False

    def reset(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._cand.clear()
            self._errors.clear()
            self._sent.clear()
            self._fb_busy.clear()
            self._fb_next.clear()
            self._limited.clear()
            for job in self._jobs.values():
                job["at"] = job["next"] = job["ok_at"] = job["done_at"] = 0.0
            self._asked = 0.0


SERVICE = QuoteService()
