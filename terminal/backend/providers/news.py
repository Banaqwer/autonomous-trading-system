"""News feeds for the NEWS page (owner: S6): the Alpaca news API and keyless RSS/Atom feeds.

Sources verified 2026-11-12 (terminal/audit/live-sources.json + this slice's own probes):

* Alpaca `v1beta1/news` (Benzinga and others; real time; keys in headers; shares the
  200 requests/minute allowance of the market-data API). Equity-centric.
* RSS 2.0 / Atom feeds, no key: OilPrice, EIA Today in Energy, EIA press, MarketWatch top
  stories, WSJ Markets, FT Markets, Nasdaq Markets, BBC Business, Federal Reserve press,
  ECB press, CoinDesk, Cointelegraph. (CNBC, Yahoo and FXStreet RSS refused scripted
  clients: 403/404.)

Everything a feed sends is UNTRUSTED text. Titles and summaries are reduced to plain text
here (tags stripped, entities decoded, whitespace collapsed, length capped) and the page
sets them with textContent; a link is passed on only when it is an http(s) URL.
Documents that declare a DTD or entities are refused before parsing (entity-expansion
attacks). One function per source touches the network; the parsers are pure.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

from core import http
from core.errors import BadRequest, NotConfigured, UpstreamError
from core.util import iso_epoch

ALPACA_BASE = "https://data.alpaca.markets"
ALPACA_SOURCE = "alpaca-news"
RSS_SOURCE = "rss"
MAX_TITLE, MAX_SUMMARY, MAX_SYMBOLS = 300, 600, 12

# key -> feed. `topic` is the topic every item of the feed belongs to; keywords add more.
FEEDS = {
    "oilprice":      {"label": "OilPrice.com",          "topic": "energy",  "url": "https://oilprice.com/rss/main"},
    "eia-today":     {"label": "EIA Today in Energy",   "topic": "energy",  "url": "https://www.eia.gov/rss/todayinenergy.xml"},
    "eia-press":     {"label": "EIA press releases",    "topic": "energy",  "url": "https://www.eia.gov/rss/press_rss.xml"},
    "marketwatch":   {"label": "MarketWatch",           "topic": "markets", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    "wsj-markets":   {"label": "WSJ Markets",           "topic": "markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    "ft-markets":    {"label": "FT Markets",            "topic": "markets", "url": "https://www.ft.com/markets?format=rss"},
    "nasdaq":        {"label": "Nasdaq Markets",        "topic": "markets", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
    "bbc-business":  {"label": "BBC Business",          "topic": "macro",   "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    "fed":           {"label": "Federal Reserve",       "topic": "macro",   "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    "ecb":           {"label": "ECB",                   "topic": "macro",   "url": "https://www.ecb.europa.eu/rss/press.html"},
    "coindesk":      {"label": "CoinDesk",              "topic": "crypto",  "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    "cointelegraph": {"label": "Cointelegraph",         "topic": "crypto",  "url": "https://cointelegraph.com/rss"},
}

TOPICS = ("energy", "macro", "markets", "equities", "fx", "crypto", "filings")

# vendor ticker -> terminal instrument id
SYMBOL_IDS = {"AAPL": "AAPL", "NVDA": "NVDA", "TSLA": "TSLA", "XOM": "XOM", "SPY": "SPX", "QQQ": "NDX",
              "IWM": "RUT", "USO": "CL", "UNG": "NG", "GLD": "GC", "BTCUSD": "BTC", "ETHUSD": "ETH",
              "SOLUSD": "SOL", "BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}
# symbols asked from Alpaca so that the instrument filter has something to show
WATCH_SYMBOLS = ("AAPL", "NVDA", "TSLA", "XOM", "SPY", "QQQ", "IWM", "USO", "UNG", "GLD")

_ID_WORDS = {
    "CL": r"crude|\bWTI\b|\bbrent\b|\bOPEC\b|oil price|\boil\b|barrels?\b",
    "NG": r"natural gas|\bLNG\b|henry hub",
    "GC": r"\bgold\b|bullion",
    "HG": r"\bcopper\b",
    "RB": r"gasoline|\bRBOB\b",
    "EURUSD": r"\beuro\b|\bECB\b|EUR/USD|euro ?zone|euro area",
    "USDJPY": r"\byen\b|bank of japan|\bBoJ\b|USD/JPY",
    "GBPUSD": r"sterling|\bpound\b|bank of england|\bBoE\b|GBP/USD",
    "AUDUSD": r"australian dollar|\bRBA\b|\baussie\b|AUD/USD",
    "US10Y": r"treasur(?:y|ies)|bond yields?|10-year|\bFOMC\b|federal reserve|\bFed\b",
    "US2Y": r"treasur(?:y|ies)|2-year|\bFOMC\b|federal reserve|\bFed\b",
    "DE10Y": r"\bbunds?\b|german (?:bond|yield)",
    "SPX": r"S&P 500|S&amp;P 500|wall street",
    "NDX": r"\bnasdaq\b",
    "VIX": r"\bVIX\b|volatility index",
    "RUT": r"russell 2000|small[- ]caps?",
    "AAPL": r"\bapple\b|\biphone\b", "NVDA": r"\bnvidia\b", "TSLA": r"\btesla\b", "XOM": r"\bexxon",
    "BTC": r"\bbitcoin\b|\bBTC\b", "ETH": r"\bethereum\b|\bether\b|\bETH\b", "SOL": r"\bsolana\b",
}
_ID_RE = {k: re.compile(v, re.I) for k, v in _ID_WORDS.items()}
_TOPIC_RE = {
    "energy": re.compile(r"crude|\boil\b|\bOPEC\b|natural gas|\bLNG\b|refiner|pipeline|gasoline|diesel|"
                         r"\benergy\b|barrels?\b|\bEIA\b|petroleum|\bbrent\b|\bWTI\b", re.I),
    "macro": re.compile(r"\bFed\b|federal reserve|\bFOMC\b|\bECB\b|central bank|inflation|\bCPI\b|\bPPI\b|\bGDP\b|"
                        r"payrolls|jobless|unemployment|tariffs?\b|interest rates?|rate (?:cut|hike)|recession|"
                        r"treasur(?:y|ies)|bank of (?:england|japan)|monetary policy", re.I),
    "fx": re.compile(r"\bdollar\b|\byen\b|\beuro\b|sterling|\bforex\b|currenc(?:y|ies)|EUR/USD|USD/JPY|GBP/USD", re.I),
    "crypto": re.compile(r"bitcoin|\bBTC\b|ethereum|\bcrypto|stablecoin|solana|blockchain|\bETH\b", re.I),
}
_TAG = re.compile(r"<[^>]{0,2000}>")
_WS = re.compile(r"\s+")


# ------------------------------------------------------------------ text hygiene
def clean_text(value, limit: int) -> str:
    """Untrusted markup -> one line of plain text."""
    if not isinstance(value, str):
        return ""
    text = html.unescape(_TAG.sub(" ", html.unescape(value)))
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = _WS.sub(" ", text).strip()
    return text[:limit - 1].rstrip() + "…" if len(text) > limit else text


def safe_url(value):
    """The URL when it is plain http(s) with a host, else None (never javascript:, data:, …)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 2000 or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return value


def item_id(provider: str, url, title: str, t) -> str:
    return hashlib.sha1(f"{provider}|{url or ''}|{title}|{int(t or 0)}".encode("utf-8", "replace")).hexdigest()[:16]


def tag(item: dict, feed_topic: str | None = None) -> dict:
    """Add `ids` (terminal instruments the item is about: by ticker, else by keyword in the
    title) and `topics` (the feed's own topic plus keyword matches in title and summary)."""
    title = item.get("title") or ""
    text = title + " " + (item.get("summary") or "")
    ids = []
    for sym in item.get("symbols") or []:
        iid = SYMBOL_IDS.get(sym)
        if iid and iid not in ids:
            ids.append(iid)
    for iid, rx in _ID_RE.items():
        if iid not in ids and rx.search(title):
            ids.append(iid)
    topics = [feed_topic] if feed_topic else []
    for topic, rx in _TOPIC_RE.items():
        if topic not in topics and rx.search(text):
            topics.append(topic)
    if item.get("symbols") and "equities" not in topics and feed_topic != "crypto":
        topics.append("equities")
    item["ids"], item["topics"] = ids, topics
    return item


# ------------------------------------------------------------------ Alpaca
def alpaca_configured() -> bool:
    return bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))


def fetch_alpaca(symbols=None, limit: int = 50, timeout: float = 12) -> dict:
    """Raw `{"news": [...]}`, newest first. `symbols` narrows it to those tickers."""
    if not alpaca_configured():
        raise NotConfigured("APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set", source=ALPACA_SOURCE)
    params = {"limit": max(1, min(int(limit), 50)), "sort": "desc", "include_content": "false"}
    if symbols:
        symbols = [s for s in symbols if re.match(r"^[A-Z.]{1,8}$", s or "")]
        if not symbols:
            raise BadRequest("no valid symbol")
        params["symbols"] = ",".join(symbols[:20])
    headers = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
               "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    return http.get_json(f"{ALPACA_BASE}/v1beta1/news?" + http.qs(params), headers, timeout, source=ALPACA_SOURCE)


def parse_alpaca(payload) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("news"), list):
        raise UpstreamError(ALPACA_SOURCE, None, "unexpected reply shape (no news list)")
    out = []
    for n in payload["news"]:
        if not isinstance(n, dict):
            continue
        title, t = clean_text(n.get("headline"), MAX_TITLE), iso_epoch(n.get("created_at"))
        if not title or t is None:
            continue
        symbols = [s for s in (n.get("symbols") or []) if isinstance(s, str) and re.match(r"^[A-Z0-9./-]{1,12}$", s)]
        url = safe_url(n.get("url"))
        out.append(tag({"id": item_id("alpaca", url, title, t), "t": t, "provider": "alpaca",
                        "source": clean_text(n.get("source"), 40) or "alpaca", "title": title, "url": url,
                        "symbols": symbols[:MAX_SYMBOLS], "summary": clean_text(n.get("summary"), MAX_SUMMARY)}))
    return out


# ------------------------------------------------------------------ RSS / Atom
def fetch_rss(key: str, timeout: float = 10) -> bytes:
    """The feed as BYTES: the XML parser must see the document's own encoding declaration
    (the EIA feeds are ISO-8859-1)."""
    feed = FEEDS.get(key)
    if not feed:
        raise BadRequest(f"unknown feed {key!r}")
    return http.get_bytes(feed["url"], {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
                          timeout, source=RSS_SOURCE + "-" + key)


def _local(tag_name: str) -> str:
    return tag_name.rsplit("}", 1)[-1].lower()


def _when(text):
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    t = iso_epoch(text)
    if t is not None:
        return t
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        import datetime
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def parse_rss(text, key: str) -> list:
    """RSS 2.0 `<item>` / Atom `<entry>` -> items. `text` is the feed as bytes (parsed with its
    own encoding declaration) or as an already decoded string. An item without a title or a
    readable date is dropped (a headline with an invented time would be a fabricated datum)."""
    feed = FEEDS.get(key) or {"label": key, "topic": None}
    src = RSS_SOURCE + "-" + key
    if isinstance(text, str):                      # already decoded: the declaration must not claim another encoding
        raw = re.sub(r"^(<\?xml[^>]*?)\s+encoding=[\"'][^\"']*[\"']", r"\1", text.lstrip("\ufeff \r\n\t"), count=1).encode("utf-8")
    elif isinstance(text, (bytes, bytearray)):
        raw = bytes(text).lstrip(b"\xef\xbb\xbf \r\n\t")
    else:
        raw = b""
    if not raw.strip():
        raise UpstreamError(src, None, "empty feed")
    probe = raw.decode("latin-1").lower()
    if "<!doctype" in probe[:4000] or "<!entity" in probe:
        raise UpstreamError(src, None, "feed declares a DTD or entities: refused")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise UpstreamError(src, None, f"feed is not well-formed XML ({str(e)[:80]})") from None
    out = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields: dict = {}
        for child in node:
            name = _local(child.tag)
            if name == "link":
                href = child.attrib.get("href")
                if href and child.attrib.get("rel", "alternate") == "alternate":
                    fields.setdefault("link", href)
                elif child.text and child.text.strip():
                    fields.setdefault("link", child.text)
            elif name in ("title", "pubdate", "published", "updated", "date", "description", "summary", "guid", "id"):
                fields.setdefault(name, child.text or "")
        title = clean_text(fields.get("title"), MAX_TITLE)
        t = _when(fields.get("pubdate")) or _when(fields.get("published")) or _when(fields.get("date")) \
            or _when(fields.get("updated"))
        if not title or t is None:
            continue
        url = safe_url(fields.get("link")) or safe_url(fields.get("guid")) or safe_url(fields.get("id"))
        out.append(tag({"id": item_id(key, url, title, t), "t": t, "provider": "rss", "source": feed["label"],
                        "title": title, "url": url, "symbols": [],
                        "summary": clean_text(fields.get("description") or fields.get("summary"), MAX_SUMMARY)},
                       feed.get("topic")))
    out.sort(key=lambda x: -x["t"])
    return out
