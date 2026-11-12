"""Deribit public API (keyless) — option chains for BTC, ETH and SOL (owner: S3 options).

ONE call returns a whole currency (every expiry, every strike):

    GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option

* BTC / ETH options are coin-margined: `bid_price`, `ask_price`, `mark_price` are in COIN. The USD
  price is coin price x `underlying_price` (the forward of that row's expiry) — this is the
  convention Deribit's own pricing uses (price_coin = Black-76(F, K, …) / F).
* SOL options are USDC-settled linear options: `currency=USDC`, instruments `SOL_USDC-<expiry>-
  <strike>-<C|P>`, prices already in USDC. The same answer also carries BTC_USDC, ETH_USDC, XRP_USDC …
  rows, which are filtered out by the instrument prefix.
* `mark_iv` is in percent. `open_interest` and `volume` are in units of the base coin (also for
  SOL_USDC, whose contract size is 10 SOL: volume_usd / volume equals the premium per 1 SOL).
* `estimated_delivery_price` is the Deribit index (btc_usd, eth_usd, sol_usdc) — the spot reference
  options settle against. `underlying_price` is the forward for the row's expiry.
* The summary has NO greeks (they would cost one `ticker` call per instrument): the route derives
  them with Black-76 from `mark_iv` and `underlying_price` (r = 0, as Deribit's `interest_rate`).
* Expiry is 08:00 UTC on the date in the instrument name (`25DEC26`, `4DEC26`).

`fetch_summary` is the only function that touches the network; `parse_summary` is pure.
"""
from __future__ import annotations

import calendar
import datetime
import re

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import num

BASE = "https://www.deribit.com/api/v2"
SOURCE = "deribit"
EXPIRY_HOUR_UTC = 8

# vendor symbol (universe `options.symbol`) -> (Deribit currency, instrument prefix, native price unit)
MARKETS = {
    "BTC": ("BTC", "BTC-", "BTC"),
    "ETH": ("ETH", "ETH-", "ETH"),
    "SOL_USDC": ("USDC", "SOL_USDC-", "USDC"),
}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
_NAME = re.compile(r"^([A-Z]+(?:_[A-Z]+)?)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:d\d+)?)-([CP])$")


def market(symbol: str) -> tuple[str, str, str]:
    """(currency, instrument prefix, native unit) for a universe options symbol; BadRequest otherwise.
    Only these three literals can ever reach the upstream URL."""
    try:
        return MARKETS[symbol]
    except KeyError:
        raise BadRequest(f"Deribit options are wired for {', '.join(MARKETS)} only") from None


def fetch_summary(currency: str, timeout: float = 15) -> dict:
    """The raw JSON-RPC answer for one currency (all its option instruments)."""
    if currency not in {c for c, _, _ in MARKETS.values()}:
        raise BadRequest("unknown Deribit currency")
    url = f"{BASE}/public/get_book_summary_by_currency?{http.qs({'currency': currency, 'kind': 'option'})}"
    return http.get_json(url, timeout=timeout, source=SOURCE)


def parse_name(name: str):
    """'BTC-25DEC26-74000-C' -> ('BTC', '2026-12-25', expiry epoch s, 74000.0, 'C'); None if not an option name.
    Strikes with decimals are written with a 'd' ('0d5' = 0.5)."""
    m = _NAME.match(name or "")
    if not m:
        return None
    base, day, mon, yy, strike, cp = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    year, day = 2000 + int(yy), int(day)
    try:
        datetime.date(year, month, day)                       # refuses 31 FEB instead of rolling it over
    except ValueError:
        return None
    exp_ts = calendar.timegm((year, month, day, EXPIRY_HOUR_UTC, 0, 0))
    k = num(strike.replace("d", "."))
    if not k or k <= 0:
        return None
    return base, f"{year:04d}-{month:02d}-{day:02d}", float(exp_ts), k, cp


def parse_summary(payload: dict, symbol: str) -> dict:
    """Normalise one book summary to the chain snapshot the options route works on:

        {"source", "symbol", "as_of" (Deribit's clock, epoch s), "spot" (Deribit index), "spot_src",
         "native" ("BTC" | "ETH" | "USDC"), "contracts": [contract, …]}
        contract = {"name", "key" ("YYYY-MM-DD"), "date", "root", "exp_ts", "strike", "type" ("C"|"P"),
                    "bid", "ask", "mark" (USD), "bid_native", "ask_native", "mark_native" (as quoted),
                    "iv" (decimal), "oi", "volume" (base-coin units), "fwd" (that row's forward),
                    "last" (USD), "delta"/"gamma"/"vega"/"theta": None (not in this answer)}

    A row without a usable name is skipped; a missing number is None, never 0."""
    _, prefix, native = market(symbol)
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        err = payload.get("error") if isinstance(payload, dict) else None
        msg = (err or {}).get("message") if isinstance(err, dict) else None
        raise UpstreamError(SOURCE, None, f"unexpected reply from Deribit{': ' + str(msg)[:120] if msg else ''}")
    coin = native != "USDC"
    contracts, spot = [], None
    for row in payload["result"]:
        if not isinstance(row, dict):
            continue
        name = row.get("instrument_name") or ""
        if not name.startswith(prefix):
            continue
        parsed = parse_name(name)
        if parsed is None:
            continue
        _, date, exp_ts, strike, cp = parsed
        fwd = num(row.get("underlying_price"))
        if not fwd or fwd <= 0:
            fwd = None
        fx = fwd if coin else 1.0             # coin price -> USD; None when there is no forward

        def usd(v, fx=fx):
            v = num(v)
            return None if v is None or fx is None else v * fx

        iv = num(row.get("mark_iv"))
        if spot is None:
            spot = num(row.get("estimated_delivery_price")) or None
        contracts.append({
            "name": name, "key": date, "date": date, "root": prefix.rstrip("-"), "exp_ts": exp_ts,
            "strike": strike, "type": cp,
            "bid": usd(row.get("bid_price")), "ask": usd(row.get("ask_price")), "mark": usd(row.get("mark_price")),
            "bid_native": num(row.get("bid_price")), "ask_native": num(row.get("ask_price")),
            "mark_native": num(row.get("mark_price")),
            "iv": iv / 100.0 if iv and iv > 0 else None,
            "oi": num(row.get("open_interest")), "volume": num(row.get("volume")),
            "fwd": fwd, "last": usd(row.get("last")), "last_t": None,
            "delta": None, "gamma": None, "vega": None, "theta": None,
        })
    us_out = num(payload.get("usOut"))
    return {
        "source": SOURCE, "symbol": symbol, "as_of": us_out / 1e6 if us_out else None,
        "spot": spot, "spot_src": "Deribit index (settlement reference)",
        "native": native, "contracts": contracts,
    }
