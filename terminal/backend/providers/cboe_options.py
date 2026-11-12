"""CBOE delayed quotes — option chains for US equities and index options (keyless; owner: S3 options).

ONE file holds every expiry and strike of an underlying:

    GET https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json      (stocks)
    GET https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json      (indices: leading underscore)

* 15 minutes delayed. `timestamp` (top level, UTC) is when CBOE wrote the file: for a stock it stops
  moving outside the US session (hours old overnight); the index files (SPX, VIX trade almost round
  the clock) are rewritten on request. The route labels the payload from that timestamp.
* `data.current_price` is the (equally delayed) underlying price that belongs to these quotes.
* `data.options[]`: `option` is the OCC symbol — root, YYMMDD, C|P, strike x 1000 in 8 digits
  ("AAPL261120C00355000", "SPXW261120P07570000"). One file can hold several roots that expire on
  the same date (SPX = AM-settled monthly, SPXW = PM-settled weekly/daily, VIX / VIXW …): a series
  is (date, root), never the date alone.
* Per row: bid, ask (0 = no quote), iv (fraction; 0 = CBOE computed none), open_interest (contracts,
  the PRIOR day's), volume (today's contracts), delta, gamma, vega (per 1 vol point), theta (per
  calendar day), last_trade_price, last_trade_time (US Eastern, no offset). One contract = 100 units.
* Files are large (SPX about 14 MB, 31,000 contracts): fetch through core.cache for minutes, never
  per page request, and send the page only what it draws.

`fetch_chain` is the only function that touches the network; `parse_chain` is pure.
"""
from __future__ import annotations

import datetime
import re

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import iso_epoch, num

BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
SOURCE = "cboe"
CONTRACT_SIZE = 100.0
# roots whose final settlement is the morning of the expiry date (special opening quotation);
# everything else stops at the closing bell of that date
AM_SETTLED = frozenset({"SPX", "NDX", "RUT", "VIX", "VIXW", "DJX", "OEX", "XEO"})

_SYMBOL = re.compile(r"^_?[A-Z]{1,6}$")
_OCC = re.compile(r"^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def fetch_chain(symbol: str, timeout: float = 40) -> dict:
    """The raw CBOE file for a vendor symbol ("AAPL", "_SPX")."""
    if not _SYMBOL.match(symbol or ""):
        raise BadRequest("not a CBOE options symbol")
    return http.get_json(f"{BASE}/options/{symbol}.json", timeout=timeout, source=SOURCE)


def parse_occ(option: str):
    """'SPXW261120P07570000' -> ('SPXW', '2026-11-20', 'P', 7570.0); None if it is not an OCC symbol."""
    m = _OCC.match(option or "")
    if not m:
        return None
    root, yy, mm, dd, cp, strike = m.groups()
    try:
        datetime.date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None
    k = int(strike) / 1000.0
    if k <= 0:
        return None
    return root, f"20{yy}-{mm}-{dd}", cp, k


def _pos(v):
    """A price / IV that CBOE reports as 0 when there is none -> None."""
    v = num(v)
    return v if v is not None and v > 0 else None


def parse_chain(payload: dict, symbol: str, eastern_to_epoch=None, expiry_epoch=None) -> dict:
    """Normalise one CBOE file to the chain snapshot the options route works on (same shape as
    providers.deribit.parse_summary):

        {"source", "symbol", "as_of" (file timestamp, epoch s), "spot", "spot_src", "native": "USD",
         "contracts": [{"name", "key", "date", "root", "exp_ts", "strike", "type", "bid", "ask", "mark",
                        "iv", "oi", "volume", "fwd": None, "last", "last_t",
                        "delta", "gamma", "vega", "theta"}, …]}

    `key` is the date ("2026-11-20") when one root expires that day, else "date.ROOT"
    ("2026-11-20.SPXW"). `mark` is the bid/ask midpoint (CBOE has no mark price); None unless both
    sides are quoted. `expiry_epoch(date, am_settled)` gives the expiry instant (epoch s) and
    `eastern_to_epoch(naive_iso)` converts last_trade_time; without them those fields are None."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("options"), list):
        raise UpstreamError(SOURCE, None, "unexpected reply from CBOE (no data.options)")
    parsed = []
    roots_by_date: dict[str, set] = {}
    for row in data["options"]:
        if not isinstance(row, dict):
            continue
        occ = parse_occ(row.get("option"))
        if occ is None:
            continue
        root, date, cp, strike = occ
        roots_by_date.setdefault(date, set()).add(root)
        parsed.append((row, root, date, cp, strike))
    exp_cache: dict[tuple, float | None] = {}
    contracts = []
    for row, root, date, cp, strike in parsed:
        am = root in AM_SETTLED
        if (date, am) not in exp_cache:
            exp_cache[(date, am)] = expiry_epoch(date, am) if expiry_epoch else None
        bid, ask = _pos(row.get("bid")), _pos(row.get("ask"))
        iv = _pos(row.get("iv"))
        has_greeks = iv is not None            # iv 0 = CBOE's model did not run: its greeks are placeholders
        ltt = row.get("last_trade_time")
        contracts.append({
            "name": row["option"], "key": date if len(roots_by_date[date]) == 1 else f"{date}.{root}",
            "date": date, "root": root, "exp_ts": exp_cache[(date, am)], "am_settled": am,
            "strike": strike, "type": cp,
            "bid": bid, "ask": ask,
            "mark": (bid + ask) / 2.0 if bid is not None and ask is not None and ask >= bid else None,
            "iv": iv, "oi": num(row.get("open_interest")), "volume": num(row.get("volume")),
            "fwd": None, "last": _pos(row.get("last_trade_price")),
            "last_t": eastern_to_epoch(ltt) if (eastern_to_epoch and isinstance(ltt, str) and ltt) else None,
            "delta": num(row.get("delta")) if has_greeks else None,
            "gamma": num(row.get("gamma")) if has_greeks else None,
            "vega": num(row.get("vega")) if has_greeks else None,
            "theta": num(row.get("theta")) if has_greeks else None,
        })
    return {
        "source": SOURCE, "symbol": symbol, "as_of": iso_epoch(payload.get("timestamp")),
        "spot": _pos(data.get("current_price")), "spot_src": "CBOE delayed underlying price",
        "native": "USD", "contracts": contracts,
    }
