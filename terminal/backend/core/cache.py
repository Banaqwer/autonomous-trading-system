"""TTL cache in front of every upstream call (REBUILD_SPEC §3.1, rule 6).

    from core.cache import cached
    data = cached("yahoo:spark", 14, lambda: yahoo.spark(symbols), stale_ttl=300)

* TTL            a value younger than `ttl` seconds is returned without calling `fn`.
* single-flight  concurrent callers of one key share ONE `fn()` call (and its result
                 or its exception); they never queue up a second upstream call.
* stale-on-error if `fn` raises and an older value exists that is at most
                 `ttl + stale_ttl` old, that value is served instead. A dict payload
                 is returned as a copy marked `"stale": true` and
                 `"error": {code, message, source?}`; the cached original is untouched.
* no poisoning   an exception is never stored as data. A failure is remembered only
                 as a failure, for `error_ttl` seconds (default min(ttl, 5)), during
                 which callers get the stale value / the same error without hitting
                 the source again (an outage must not turn into a request storm).
* bounded        at most MAX_ENTRIES keys, least-recently-used evicted.

`key` is "<source>:<what>"; the part before the first colon names the row on the
/api/status board that counts cache hits (override with source=).

Thread-safe: plain `def` routes run in the threadpool. From async code use
`await asyncio.to_thread(cached, key, ttl, fn)`.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from core import errors, status

MAX_ENTRIES = 512
FLIGHT_WAIT = 120.0          # a follower never waits longer than this for the leader


class _Flight:
    __slots__ = ("done", "value", "exc")

    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.exc: BaseException | None = None


class _Entry:
    __slots__ = ("has_value", "value", "at", "error", "error_at", "flight")

    def __init__(self):
        self.has_value = False
        self.value = None
        self.at = 0.0
        self.error: BaseException | None = None
        self.error_at = 0.0
        self.flight: _Flight | None = None


@dataclass
class Cached:
    """What `fetch` returns: the value plus how it was obtained."""
    value: object
    at: float                      # epoch s when the value was fetched from the source
    stale: bool = False            # True: served because the refresh failed
    error: dict | None = None      # {code, message, source?} of that failure
    hit: bool = False              # True: no upstream call was made for this request

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.at)


_lock = threading.Lock()
_entries: "OrderedDict[str, _Entry]" = OrderedDict()


def _default_stale(ttl: float) -> float:
    return max(10.0 * ttl, 60.0)


def _evict_locked() -> None:
    if len(_entries) <= MAX_ENTRIES:
        return
    for key in list(_entries):
        if len(_entries) <= MAX_ENTRIES:
            break
        if _entries[key].flight is None:
            del _entries[key]


def fetch(key: str, ttl: float, fn, stale_ttl: float | None = None, *,
          source: str | None = None, error_ttl: float | None = None) -> Cached:
    """Like `cached`, but returns a `Cached` record (value, at, stale, error, hit)."""
    src = source or key.split(":", 1)[0]
    stale_for = _default_stale(ttl) if stale_ttl is None else stale_ttl
    err_for = min(ttl, 5.0) if error_ttl is None else error_ttl

    def stale_or_raise(entry: _Entry, exc: BaseException) -> Cached:
        if entry.has_value and time.time() - entry.at <= ttl + stale_for:
            status.hit(src)
            return Cached(entry.value, entry.at, stale=True, error=errors.describe(exc), hit=True)
        raise exc

    with _lock:
        now = time.time()
        entry = _entries.get(key)
        if entry is None:
            entry = _entries[key] = _Entry()
        _entries.move_to_end(key)
        if entry.has_value and now - entry.at < ttl:
            status.hit(src)
            return Cached(entry.value, entry.at, hit=True)
        recent_failure = entry.error if (entry.error is not None
                                         and now - entry.error_at < err_for) else None
        flight, leader = entry.flight, False
        if recent_failure is None and flight is None:
            flight = entry.flight = _Flight()
            leader = True
        _evict_locked()

    if recent_failure is not None:
        return stale_or_raise(entry, recent_failure)

    if not leader:
        flight.done.wait(FLIGHT_WAIT)
        if flight.exc is not None:
            return stale_or_raise(entry, flight.exc)
        if not flight.done.is_set():
            return stale_or_raise(entry, errors.UpstreamError(src, None, "still waiting for the source"))
        status.hit(src)
        return Cached(flight.value, entry.at, hit=True)

    try:
        value = fn()
    except BaseException as exc:
        with _lock:
            entry.flight = None
            if isinstance(exc, Exception):
                entry.error, entry.error_at = exc, time.time()
        flight.exc = exc
        flight.done.set()
        if not isinstance(exc, Exception):          # KeyboardInterrupt / SystemExit
            raise
        return stale_or_raise(entry, exc)
    with _lock:
        entry.value, entry.has_value, entry.at = value, True, time.time()
        entry.error, entry.flight = None, None
        at = entry.at
    flight.value = value
    flight.done.set()
    return Cached(value, at)


def cached(key: str, ttl: float, fn, stale_ttl: float | None = None, *,
           source: str | None = None, error_ttl: float | None = None):
    """Return `fn()`'s result, cached for `ttl` seconds (see the module docstring).

    Raises whatever `fn` raised when there is nothing (recent enough) to serve."""
    got = fetch(key, ttl, fn, stale_ttl, source=source, error_ttl=error_ttl)
    if got.stale and isinstance(got.value, dict):
        return {**got.value, "stale": True, "error": got.error}
    return got.value


def peek(key: str) -> Cached | None:
    """The cached value for `key` regardless of age, without calling anything."""
    with _lock:
        entry = _entries.get(key)
        if entry is None or not entry.has_value:
            return None
        return Cached(entry.value, entry.at, hit=True)


def invalidate(key: str) -> None:
    with _lock:
        entry = _entries.get(key)
        if entry is not None and entry.flight is None:
            del _entries[key]


def clear() -> None:
    with _lock:
        for key in [k for k, e in _entries.items() if e.flight is None]:
            del _entries[key]


def stats() -> dict:
    with _lock:
        return {"entries": len(_entries), "max_entries": MAX_ENTRIES}
