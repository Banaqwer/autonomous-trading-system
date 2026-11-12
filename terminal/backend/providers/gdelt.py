"""GDELT 2.0 Doc API — global news + tone (owner: S6). Keyless, and NOT dependable: every
call during the 2026-11-12 audit was throttled (HTTP 429, "one request every 5 seconds"),
reset, or answered with a plain-text complaint. The terminal's NEWS page does not use
it; this adapter stays for the research app (quantpad), which treats a failure as "no
articles". OR-queries must be wrapped in parentheses or GDELT answers with text.
"""
from __future__ import annotations

from core import http

SOURCE = "gdelt"


def news(query="crude oil OR OPEC", max_records=40, mode="ArtList"):
    q = str(query)[:200]
    if " OR " in q and not q.lstrip().startswith("("):
        q = f"({q})"
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + http.qs(
        {"query": q, "mode": mode if mode in ("ArtList", "ToneChart", "TimelineTone") else "ArtList",
         "maxrecords": max(1, min(int(max_records), 250)), "format": "json", "sort": "datedesc"})
    d = http.get_json(url, timeout=25, source=SOURCE)
    return [{"title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"),
             "seendate": a.get("seendate"), "lang": a.get("language"),
             "tone": a.get("tone")} for a in (d.get("articles", []) if isinstance(d, dict) else [])]
