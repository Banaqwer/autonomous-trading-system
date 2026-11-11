"""GDELT 2.0 Doc API — global news + tone (NEWS module). No key.
"""
from __future__ import annotations
import json, urllib.request, urllib.parse

def news(query="crude oil OR OPEC", max_records=40, mode="ArtList"):
    q = urllib.parse.quote(query)
    url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}"
           f"&mode={mode}&maxrecords={max_records}&format=json&sort=datedesc")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"openterm/0.1"}), timeout=25) as r:
        d = json.loads(r.read())
    return [{"title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"),
             "seendate": a.get("seendate"), "lang": a.get("language"),
             "tone": a.get("tone")} for a in d.get("articles", [])]
