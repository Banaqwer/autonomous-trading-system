"""SEC EDGAR — company filings (owner: S6). Keyless.

The SEC asks every scripted client for a descriptive User-Agent with a contact. The real
one comes from the environment (SEC_USER_AGENT, set in config.py); the default in code is
generic on purpose — no personal address belongs in the repository.

* `fetch_search` / `parse_search`: EDGAR full-text search (efts.sec.gov), filtered by form,
  date range, free text and/or CIKs. Verified 2026-11-12: 8-Ks of AAPL NVDA TSLA XOM over
  30 days -> 4 hits in 0.7 s. A hit has the FILING DATE only (no time of day).
* `recent_8k` / `cik_to_ticker`: the "current events" Atom feed and the CIK -> ticker map,
  used by the research app (quantpad). Both return an empty result on failure, as before.
"""
from __future__ import annotations

import os
import re
import time

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import date_epoch

SOURCE = "edgar"
SEARCH = "https://efts.sec.gov/LATEST/search-index"
DEFAULT_UA = "OpenTerminal gateway (set SEC_USER_AGENT to your own contact)"
# terminal equities -> SEC CIK (10 digits)
CIKS = {"AAPL": "0000320193", "NVDA": "0001045810", "TSLA": "0001318605", "XOM": "0000034088"}
ITEMS_8K = {
    "1.01": "Entry into a material agreement", "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership", "1.05": "Material cybersecurity incident",
    "2.01": "Completion of an acquisition or disposal", "2.02": "Results of operations (earnings)",
    "2.03": "New direct financial obligation", "2.04": "Triggering event on an obligation",
    "2.05": "Exit or disposal costs", "2.06": "Material impairment",
    "3.01": "Delisting notice", "3.02": "Unregistered sale of equity", "3.03": "Change to holders' rights",
    "4.01": "Change of accountant", "4.02": "Non-reliance on prior financial statements",
    "5.01": "Change in control", "5.02": "Departure or appointment of directors or officers",
    "5.03": "Amendment to articles or bylaws", "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure", "8.01": "Other events", "9.01": "Financial statements and exhibits",
}
_FORM = re.compile(r"^[A-Z0-9][A-Z0-9 /\-]{0,11}$")
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_QUERY = re.compile(r"^[\w .,'\"()\-]{0,80}$")
_CIK = re.compile(r"^\d{1,10}$")
_NAME = re.compile(r"^(.*?)\s*(?:\(([A-Z0-9.,\- ]{1,40})\))?\s*\(CIK (\d{10})\)\s*$")


def _ua() -> dict:
    return {"User-Agent": os.environ.get("SEC_USER_AGENT") or DEFAULT_UA}


# ------------------------------------------------------------------ full-text search
def check_search(q: str, forms) -> list:
    """Validate the two caller-supplied parts of a search; returns the cleaned form list."""
    forms = [f.strip().upper() for f in forms if f and f.strip()]
    if not forms or len(forms) > 6 or any(not _FORM.match(f) for f in forms):
        raise BadRequest("forms must be 1–6 form types such as 8-K, 10-Q, 10-K")
    if not _QUERY.match(q or ""):
        raise BadRequest("q may hold letters, digits, spaces and . , ' \" ( ) - (at most 80 characters)")
    return forms


def fetch_search(q: str = "", forms=("8-K",), start: str | None = None, end: str | None = None,
                 ciks=(), timeout: float = 15) -> dict:
    forms = check_search(q, forms)
    ciks = [c.zfill(10) for c in ciks if c]
    if any(not _CIK.match(c) for c in ciks) or len(ciks) > 10:
        raise BadRequest("ciks must be up to 10 numeric CIKs")
    end = end or time.strftime("%Y-%m-%d", time.gmtime(time.time()))
    start = start or time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
    if not _DATE.match(start) or not _DATE.match(end):
        raise BadRequest("dates must be YYYY-MM-DD")
    if not q and not ciks:
        raise BadRequest("give a search text or a company")
    params = {"q": q or "", "forms": ",".join(forms), "dateRange": "custom", "startdt": start, "enddt": end,
              "ciks": ",".join(ciks) if ciks else None}
    return http.get_json(SEARCH + "?" + http.qs(params), _ua(), timeout, source=SOURCE)


def parse_search(payload) -> list:
    """[{company, ticker, cik, form, filed, t, period, items:[{code, text}], accession, url}] newest first.
    `t` = 00:00 UTC of the filing date (EDGAR search gives no time of day)."""
    try:
        hits = payload["hits"]["hits"]
    except (KeyError, TypeError):
        raise UpstreamError(SOURCE, None, "unexpected reply shape (no hits)") from None
    out, seen = [], set()
    for h in hits if isinstance(hits, list) else []:
        s = h.get("_source") if isinstance(h, dict) else None
        if not isinstance(s, dict):
            continue
        adsh, filed = s.get("adsh"), s.get("file_date")
        t = date_epoch(filed) if isinstance(filed, str) else None
        if not isinstance(adsh, str) or not re.match(r"^\d{10}-\d\d-\d{6}$", adsh) or t is None or adsh in seen:
            continue
        seen.add(adsh)
        names = s.get("display_names") or []
        m = _NAME.match(names[0]) if names and isinstance(names[0], str) else None
        ciks = s.get("ciks") or []
        cik = (m.group(3) if m else (ciks[0] if ciks else None))
        if not cik or not str(cik).isdigit():
            continue
        ticker = (m.group(2).split(",")[0].strip() if m and m.group(2) else None)
        items = [{"code": str(c), "text": ITEMS_8K.get(str(c))} for c in (s.get("items") or []) if isinstance(c, str)]
        out.append({"company": re.sub(r"\s+", " ", m.group(1)).strip() if m else None, "ticker": ticker,
                    "cik": str(cik).zfill(10), "form": s.get("form") or s.get("file_type"), "filed": filed[:10], "t": t,
                    "period": s.get("period_ending"), "items": items, "accession": adsh,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{adsh}-index.htm"})
    out.sort(key=lambda r: (-r["t"], r["accession"]))
    return out


def latest_filings(q: str = "", forms: str = "8-K,10-Q,10-K", tickers=()):
    """Parsed full-text search over the last 30 days (the function the old /api/filings
    route called and that never existed)."""
    ciks = [CIKS[t] for t in tickers if t in CIKS]
    return parse_search(fetch_search(q, [f for f in str(forms).split(",") if f.strip()], ciks=ciks))


# ------------------------------------------------------------------ research-app helpers (old contract)
def recent_8k(count=40):
    count = max(1, min(int(count), 100))
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
           f"&company=&dateb=&owner=include&count={count}&output=atom")
    try:
        x = http.get_text(url, _ua(), 20, source=SOURCE)
    except Exception:
        return []
    out = []
    for t in re.findall(r"<title>([^<]+)</title>", x)[1:]:
        m = re.match(r"8-K\s*-\s*(.+?)\s*\((\d{10})\)", t)
        if m:
            out.append({"company": m.group(1).strip(), "cik": int(m.group(2))})
    return out


_TMAP = {"ts": 0, "map": {}}


def cik_to_ticker():
    """SEC company_tickers.json -> {cik: TICKER}. Cached 24h."""
    if _TMAP["map"] and time.time() - _TMAP["ts"] < 86400:
        return _TMAP["map"]
    try:
        d = http.get_json("https://www.sec.gov/files/company_tickers.json", _ua(), 25, source=SOURCE)
        m = {int(v["cik_str"]): v["ticker"] for v in d.values()}
        _TMAP.update(ts=time.time(), map=m)
    except Exception:
        pass
    return _TMAP["map"]
