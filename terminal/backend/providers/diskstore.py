"""Tiny JSON store on disk for answers that are expensive to ask for again (owner: S6).

The in-memory TTL cache is empty after every gateway restart. For a source with a small
daily budget (Marketaux: 100 calls a day) or a touchy rate limit (the ForexFactory
calendar file) a restart must not cost a call, so the last good answer is kept in
`backend/.cache/<name>.json` together with the time it was fetched.

    got = diskstore.load("marketaux_headlines")      # -> {"at": epoch s, "value": …} | None
    diskstore.save("marketaux_headlines", payload)   # never raises (a read-only disk is not an outage)

The directory ignores itself in git (it writes its own .gitignore). Names are restricted
to [a-z0-9_-] so they are valid file names on Windows. Never store a secret here.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
_NAME = re.compile(r"^[a-z0-9_-]{1,64}$")


def _path(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"bad store name {name!r}")
    return os.path.join(DIR, name + ".json")


def load(name: str):
    """{"at": epoch seconds, "value": …} or None (missing, unreadable, wrong shape)."""
    try:
        with open(_path(name), encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(got, dict) and isinstance(got.get("at"), (int, float)) and "value" in got:
        return got
    return None


def save(name: str, value, at: float | None = None) -> bool:
    """Write atomically (temp file + replace). False when the disk refuses."""
    path = _path(name)
    try:
        os.makedirs(DIR, exist_ok=True)
        ignore = os.path.join(DIR, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as f:
                f.write("*\n")
        fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"at": time.time() if at is None else at, "value": value}, f)
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        return False
