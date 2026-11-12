"""Per-source status board (REBUILD_SPEC §3.1), surfaced by /api/status.

    {name: {ok, last_ok_at, last_error, last_error_at, calls, errors, cache_hits}}

`core.http` records every upstream call, `core.cache` records cache hits. Providers
that do not go through core.http (SDKs, websockets) call ok()/error() themselves.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
BOARD: dict[str, dict] = {}


def _row(source: str) -> dict:
    return BOARD.setdefault(source, {
        "ok": None,               # result of the most recent upstream call (None: never called)
        "last_ok_at": None, "last_error": None, "last_error_at": None,
        "calls": 0,               # upstream calls actually made
        "errors": 0,              # ...of which failed
        "cache_hits": 0,          # requests answered from the TTL cache instead
    })


def ok(source: str) -> None:
    with _lock:
        r = _row(source)
        r["calls"] += 1
        r["ok"] = True
        r["last_ok_at"] = time.time()


def error(source: str, message) -> None:
    from core import http
    with _lock:
        r = _row(source)
        r["calls"] += 1
        r["errors"] += 1
        r["ok"] = False
        r["last_error"] = http.scrub(str(message))[:300]
        r["last_error_at"] = time.time()


def hit(source: str) -> None:
    with _lock:
        _row(source)["cache_hits"] += 1


def calls(source: str) -> int:
    with _lock:
        return _row(source)["calls"]


def snapshot() -> dict:
    with _lock:
        return {name: dict(row) for name, row in sorted(BOARD.items())}


def reset() -> None:
    with _lock:
        BOARD.clear()
