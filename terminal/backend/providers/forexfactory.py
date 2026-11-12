"""ForexFactory weekly economic calendar (owner: S6) — keyless JSON export.

    https://nfs.faireconomy.media/ff_calendar_thisweek.json

An array of `{title, country, date, impact, forecast, previous}` for the CURRENT week
(106 events on 2026-11-12). `country` is a CURRENCY code (USD, EUR, GBP, JPY, AUD, CAD,
CHF, CNY, NZD, or "All"); `date` is ISO-8601 with the US-Eastern offset; `impact` is
High / Medium / Low / Holiday. There is NO `actual` field: released values come from
official series (providers/actuals.py) or stay empty.

The host rate-limits hard (HTTP 429 with an HTML page; this machine was refused on
2026-11-12 after a handful of requests in a day). So: ask at most every two hours
(route cache), keep the last good file on disk, and after a 429 do not ask again for
at least BACKOFF seconds whatever the caller wants.
"""
from __future__ import annotations

import re
import time

from core import http
from core.errors import RateLimited, UpstreamError
from core.util import iso_epoch

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SOURCE = "forexfactory"
BACKOFF = 30 * 60
COUNTRY = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP", "AUD": "AU", "CAD": "CA", "CHF": "CH",
           "CNY": "CN", "NZD": "NZ", "ALL": "ALL"}
IMPACT = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday", "non-economic": "none"}
_blocked = {"until": 0.0}


def fetch(timeout: float = 12):
    """The raw array. Raises RateLimited (with retry_after) while the host has refused us."""
    now = time.time()
    if now < _blocked["until"]:
        raise RateLimited(SOURCE, "the calendar host rate-limited this machine; waiting before the next try",
                          retry_after=_blocked["until"] - now)
    try:
        return http.get_json(URL, timeout=timeout, source=SOURCE)
    except RateLimited as e:
        wait = max(e.retry_after or 0, BACKOFF)
        _blocked["until"] = now + wait
        raise RateLimited(SOURCE, "the calendar host rate-limits (HTTP 429); it allows only a few requests "
                                  "a day per address", retry_after=wait) from None


def _value(v):
    """'0.3%' stays as the source wrote it; '' / None -> None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v).strip()
    return v[:24] if v else None


def parse(payload) -> list:
    """[{id, t, currency, country, title, impact, forecast, previous}] ascending by time.
    Rows without a readable time or title are dropped; nothing is invented."""
    if not isinstance(payload, list):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (not a list of events)")
    out, seen = [], {}
    for e in payload:
        if not isinstance(e, dict):
            continue
        title = re.sub(r"\s+", " ", e.get("title")).strip()[:120] if isinstance(e.get("title"), str) else ""
        t = iso_epoch(e.get("date")) if isinstance(e.get("date"), str) else None
        if not title or t is None:
            continue
        currency = str(e.get("country") or "").strip().upper()[:3]
        impact = IMPACT.get(str(e.get("impact") or "").strip().lower(), "none")
        base = f"{int(t)}-{currency}-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        seen[base] = seen.get(base, 0) + 1
        out.append({"id": base if seen[base] == 1 else f"{base}-{seen[base]}", "t": t, "currency": currency or None,
                    "country": COUNTRY.get(currency, currency or None), "title": title, "impact": impact,
                    "forecast": _value(e.get("forecast")), "previous": _value(e.get("previous"))})
    out.sort(key=lambda x: (x["t"], x["id"]))
    return out
