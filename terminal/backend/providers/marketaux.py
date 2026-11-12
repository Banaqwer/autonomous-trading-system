"""Marketaux — news with per-entity sentiment (owner: S6). Set MARKETAUX_API_KEY.

FREE PLAN (measured 2026-11-12): 100 requests a DAY, 3 articles per request whatever
`limit` says, 2–28 s per answer. The reply headers carry the allowance
(`x-usagelimit-limit`, `x-usagelimit-remaining`); `fetch_headlines` returns them so the
gateway can stop before the day's budget is gone. The API takes the key only as the
`api_token` query parameter (no header form is documented); core.http scrubs it from
every error message.

`theme_sentiment` / `symbol_sentiment` are used by the research app (quantpad) and keep
their old contract: None when unconfigured or on any failure.
"""
from __future__ import annotations

import json
import os
import re

from core import http
from core.errors import NotConfigured, UpstreamError
from core.util import iso_epoch, num
from providers import news as _news

BASE = "https://api.marketaux.com/v1/news/all"
SOURCE = "marketaux"


def enabled() -> bool:
    return bool(os.environ.get("MARKETAUX_API_KEY"))


def _request(params: dict, timeout: float = 45):
    """-> (json, {"limit", "remaining"}). One billed request."""
    if not enabled():
        raise NotConfigured("MARKETAUX_API_KEY is not set", source=SOURCE)
    params = dict(params, api_token=os.environ["MARKETAUX_API_KEY"])
    code, headers, body = http.request(BASE + "?" + http.qs(params), {"Accept": "application/json"},
                                       timeout, source=SOURCE)
    low = {k.lower(): v for k, v in headers.items()}
    usage = {"limit": num(low.get("x-usagelimit-limit")), "remaining": num(low.get("x-usagelimit-remaining"))}
    try:
        return json.loads(body), usage
    except ValueError:
        raise UpstreamError(SOURCE, code, "reply is not JSON") from None


def _get(**params):
    return _request(params, timeout=30)[0]


def fetch_headlines(timeout: float = 45) -> dict:
    """{"payload": raw reply, "usage": {"limit", "remaining"}} — the newest English articles that
    name at least one listed entity (without `must_have_entities` the feed is general news)."""
    payload, usage = _request({"language": "en", "limit": 10, "filter_entities": "true",
                               "must_have_entities": "true"}, timeout)     # only articles that name a listed entity
    return {"payload": payload, "usage": usage}


def parse_headlines(payload) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (no data list)")
    out = []
    for a in payload["data"]:
        if not isinstance(a, dict):
            continue
        title, t = _news.clean_text(a.get("title"), _news.MAX_TITLE), iso_epoch(a.get("published_at"))
        if not title or t is None:
            continue
        symbols = []
        for e in a.get("entities") or []:
            sym = e.get("symbol") if isinstance(e, dict) else None
            if isinstance(sym, str) and re.match(r"^[A-Z0-9./-]{1,12}$", sym) and sym not in symbols:
                symbols.append(sym)
        url = _news.safe_url(a.get("url"))
        out.append(_news.tag({"id": _news.item_id("marketaux", url, title, t), "t": t, "provider": "marketaux",
                              "source": _news.clean_text(a.get("source"), 60) or "marketaux", "title": title,
                              "url": url, "symbols": symbols[:_news.MAX_SYMBOLS],
                              "summary": _news.clean_text(a.get("description") or a.get("snippet"), _news.MAX_SUMMARY)}))
    return out


# ------------------------------------------------------------------ research-app helpers (old contract)
def theme_sentiment(query, limit=20):
    """Average news sentiment for a theme query -> {avg, n, sample}. avg in [-1,1]."""
    if not enabled():
        return None
    try:
        d = _get(search=str(query)[:200], language="en", filter_entities="true", limit=min(int(limit), 50))
    except Exception:
        return None
    arts = d.get("data", []) if isinstance(d, dict) else []
    scores = []
    for a in arts:
        ents = a.get("entities", [])
        s = [e.get("sentiment_score") for e in ents if isinstance(e.get("sentiment_score"), (int, float))]
        if s:
            scores.append(sum(s) / len(s))
    if not scores:
        return {"avg": 0.0, "n": len(arts), "sample": arts[0]["title"] if arts else ""}
    return {"avg": sum(scores) / len(scores), "n": len(scores),
            "sample": arts[0].get("title", "") if arts else ""}


def symbol_sentiment(symbols, limit=20):
    if not enabled():
        return None
    try:
        d = _get(symbols=",".join(symbols) if isinstance(symbols, list) else str(symbols)[:200],
                 language="en", filter_entities="true", limit=min(int(limit), 50))
    except Exception:
        return None
    return d.get("data", []) if isinstance(d, dict) else []
