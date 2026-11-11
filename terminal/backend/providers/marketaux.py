"""Marketaux — news with per-entity sentiment scores. Robust replacement for the
rate-limited GDELT feed in the causal signal engine. Free tier ~100 req/day.
Set MARKETAUX_API_KEY. Docs: marketaux.com/documentation
"""
from __future__ import annotations
import os, json, urllib.request, urllib.parse

BASE = "https://api.marketaux.com/v1/news/all"

def enabled():
    return bool(os.environ.get("MARKETAUX_API_KEY"))

def _get(**params):
    params["api_token"] = os.environ["MARKETAUX_API_KEY"]
    url = BASE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "openterm/0.1"}), timeout=20) as r:
        return json.loads(r.read())

def theme_sentiment(query, limit=20):
    """Average news sentiment for a theme query -> {avg, n, sample}. avg in [-1,1]."""
    if not enabled():
        return None
    try:
        d = _get(search=query, language="en", filter_entities="true", limit=min(limit, 50))
    except Exception:
        return None
    arts = d.get("data", [])
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
        d = _get(symbols=",".join(symbols) if isinstance(symbols, list) else symbols,
                 language="en", filter_entities="true", limit=min(limit, 50))
    except Exception:
        return None
    return d.get("data", [])
