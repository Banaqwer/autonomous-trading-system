"""OPTIONS slice (S3): /api/options  /api/options/chain  /api/vol_surface  /api/gex.

Sources (from core.universe `options` = {source, symbol, delay}; nothing is hard-coded per id):
  deribit  BTC ETH SOL        one call per currency returns the whole chain; refreshed every 15 s
  cboe     AAPL NVDA TSLA XOM SPX NDX VIX RUT   one delayed file per underlying; cached for minutes
Everything else has no free options source: 404 `no_source` with the why-text (universe.require_cap).

One upstream answer is turned into ONE cached chain (`_chain`): contracts grouped into series
(expiry date + root), each with its forward, ATM IV and greeks. All four routes are views of that
cached chain, so opening OPTS, VOLS, GREEKS and GEX costs one upstream call per TTL, not four.

Conventions (stated in every payload):
  * strikes and forwards are ABSOLUTE prices everywhere. There is no "strike as a fraction of spot"
    anywhere in this file; moneyness (strike / forward) exists only as a label of the vol surface.
  * prices in USD. Deribit quotes BTC/ETH options in coin: USD = coin price x that expiry's forward.
  * iv is a decimal (0.45 = 45 %). delta per 1 unit of underlying, gamma per $1, vega in $ per 1 vol
    point, theta in $ per calendar day — all per 1 unit of the underlying (not per contract).
  * greeks come from the source when it has them (CBOE); otherwise they are DERIVED here with
    Black-76 from the row's own IV and forward (Deribit; r = 0 like Deribit's own model) and the row
    says `derived: true`.
  * GEX sign convention: dealers are assumed LONG calls and SHORT puts (customers sell calls and buy
    puts), so call gamma counts positive and put gamma negative. It is an assumption, said on the page.
    GEX sums use ONE gamma model for every source — Black-76 on the row's own IV and forward — because
    CBOE's own gammas are rounded to 4 decimals (see _gex); the sum with CBOE's gammas is returned too.
  * the underlying level (`spot`) is the source's own: Deribit's index; CBOE's delayed underlying
    price — or, when the front expiry's put-call-parity forward disagrees with that price (the cash
    index is closed while its options trade overnight; VIX options price off a future), that forward,
    with `spot_kind: "implied"` and the published price next to it. Never a price from another feed.
"""
from __future__ import annotations

import math
import re
import threading
import time

from fastapi import APIRouter, Query

from core import session, universe
from core import cache
from core.cache import cached
from core.errors import BadRequest, NoSource, UpstreamError
from providers import cboe_options, deribit

router = APIRouter()

# ════════════════════════════════ pricing maths (the only copy) ════════════════════════════════
YEAR = 365.0 * 86400.0
MIN_T = 60.0 / YEAR                      # under a minute to expiry: no greeks are derived
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(forward: float, strike: float, t: float, iv: float, cp: str, r: float = 0.0):
    """Black-76 value and greeks of a European option on a forward.

    forward, strike   ABSOLUTE prices in the same unit (never a ratio to spot)
    t                 time to expiry in years (365-day year)
    iv                implied volatility as a decimal
    cp                "C" or "P"
    r                 continuously compounded discount rate

    Returns {"price", "delta" (dV/dF), "gamma" (d2V/dF2, per 1 price unit), "vega" (per 1 vol point),
    "theta" (per calendar day)} or None when an input is unusable (non-positive, non-finite, t under
    a minute). A strike that is tiny against the forward is refused only when the logarithm cannot be
    taken — a wrong-unit strike shows up as a delta of 0 or 1, which the tests pin."""
    try:
        f, k, t, iv = float(forward), float(strike), float(t), float(iv)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(f) and math.isfinite(k) and math.isfinite(t) and math.isfinite(iv)):
        return None
    if f <= 0 or k <= 0 or iv <= 0 or t < MIN_T or cp not in ("C", "P"):
        return None
    vol = iv * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * vol * vol) / vol
    d2 = d1 - vol
    df = math.exp(-r * t)
    pdf = norm_pdf(d1)
    if cp == "C":
        price = df * (f * norm_cdf(d1) - k * norm_cdf(d2))
        delta = df * norm_cdf(d1)
    else:
        price = df * (k * norm_cdf(-d2) - f * norm_cdf(-d1))
        delta = -df * norm_cdf(-d1)
    return {
        "price": price,
        "delta": delta,
        "gamma": df * pdf / (f * vol),
        "vega": df * f * pdf * math.sqrt(t) / 100.0,
        "theta": (r * price - df * f * pdf * iv / (2.0 * math.sqrt(t))) / 365.0,
    }


def gex_usd_mm(gamma: float, oi_units: float, ref_price: float) -> float:
    """Gamma exposure of one line in $ millions per 1 % move of the underlying:
    gamma (per $1) x open interest (in units of the underlying) x price^2 x 1 % / 1e6."""
    return gamma * oi_units * ref_price * ref_price * 0.01 / 1e6


# ════════════════════════════════ the cached chain ════════════════════════════════
DERIBIT_TTL = 15            # s — one Deribit call per currency per 15 s while somebody watches
CBOE_TTL = 120              # s — CBOE files are 15 minutes delayed and large
CBOE_TTL_IDLE = 600         # s — equity files do not change outside the US session
CBOE_FRESH_S = 30 * 60      # a CBOE file older than this is last session's (label CLOSED, not D15)
CBOE_QUIET_S = 10 * 60      # no bid/ask changed between fetches this far apart: that market is not trading
FRONT_MAX_T = 45.0 / 365.0  # "front expiry" for the implied underlying level: at most 45 days out
LEVEL_TOLERANCE = 0.0015    # published price and front forward agree within 0.15 %: use the published one
GEX_UNIT = "$mm per 1% move"
PROFILE_LO, PROFILE_HI, PROFILE_STEP = 0.70, 1.30, 0.01
_EXPIRY = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.[A-Z][A-Z0-9]{0,5})?$")


class Row:
    """One contract of the cached chain (slots: a full SPX file is 31,000 of these)."""
    __slots__ = ("k", "cp", "name", "bid", "ask", "mark", "iv", "oi", "vol", "f", "delta", "gamma",
                 "vega", "theta", "derived", "last", "last_t", "bid_n", "ask_n", "mark_n", "gm")
    # gm: Black-76 gamma from the row's own IV and forward — what the gamma-exposure sums use for
    # every source (see _gex); `gamma` is what the chain shows (the source's own when it has one)


def _expiry_epoch(date: str, am_settled: bool):
    """Expiry instant of a US-listed option: the New York close of its date (13:00 on early-close
    days), or 09:30 New York for AM-settled index roots."""
    if am_settled:
        return session.local_to_epoch(f"{date}T09:30:00", session.NY)
    return session.us_close_ts(date)


def _eastern(naive_iso: str):
    return session.local_to_epoch(naive_iso, session.NY)


def _median(values):
    v = sorted(values)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _parity_forward(rows, spot):
    """Forward implied by put-call parity at the strike where the call and put mid prices are closest:
    F = K + (C - P). Undiscounted (the discount on C - P near the money is far inside the bid/ask).
    Needs both sides quoted on both options; returns (forward, strike) or (None, None)."""
    by_k: dict[float, dict] = {}
    for r in rows:
        if r.bid is not None and r.ask is not None and r.ask >= r.bid:
            by_k.setdefault(r.k, {})[r.cp] = 0.5 * (r.bid + r.ask)
    best = None
    for k, d in by_k.items():
        if "C" in d and "P" in d:
            gap = abs(d["C"] - d["P"])
            if best is None or gap < best[0]:
                best = (gap, k, d["C"] - d["P"])
    if best is None:
        return None, None
    fwd = best[1] + best[2]
    if fwd <= 0 or (spot and not (0.4 * spot <= fwd <= 2.5 * spot)):
        return None, None
    return fwd, best[1]


def _smile(rows):
    """[(strike, iv)] ascending: per strike the mean of the call and put IV that exist."""
    acc: dict[float, list] = {}
    for r in rows:
        if r.iv is not None:
            acc.setdefault(r.k, []).append(r.iv)
    return [(k, sum(v) / len(v)) for k, v in sorted(acc.items())]


def _interp(points, x, reach=0.05):
    """Linear interpolation in a sorted [(x, y)] list; outside the list the end value is used only
    within `reach` (relative) of the end, else None."""
    if not points or x is None:
        return None
    if x <= points[0][0]:
        return points[0][1] if x >= points[0][0] * (1 - reach) else None
    if x >= points[-1][0]:
        return points[-1][1] if x <= points[-1][0] * (1 + reach) else None
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= x:
            lo = mid
        else:
            hi = mid
    (x0, y0), (x1, y1) = points[lo], points[hi]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y0


def _delta_iv(rows, cp, target):
    """IV at a given delta on one side (calls: 0.25, puts: -0.25), interpolated between the two
    listed strikes whose deltas bracket it. None when the chain does not reach that delta."""
    pts = sorted((r.delta, r.iv) for r in rows
                 if r.cp == cp and r.iv is not None and r.delta is not None)
    if len(pts) < 2 or not (pts[0][0] <= target <= pts[-1][0]):
        return None
    return _interp(pts, target, reach=0.0)


def build_chain(snap: dict, fetched_at: float | None = None) -> dict:
    """Provider snapshot -> the chain every route reads. Pure (no network, no clock but `fetched_at`).

    Series whose expiry is not after the data time are dropped (CBOE keeps yesterday's expiry in the
    file until the next session), and so are series in which no contract has a quote, an IV or open
    interest (CBOE lists placeholder roots such as B1VDA). Each series gets its forward (Deribit:
    median of the rows' own forwards; CBOE: put-call parity, else the underlying level), ATM
    strike/IV, and every row that has an IV but no source greeks gets Black-76 greeks marked `derived`."""
    fetched_at = time.time() if fetched_at is None else fetched_at
    as_of = snap.get("as_of") or fetched_at
    spot = snap.get("spot")
    source = snap["source"]
    is_cboe = source == "cboe"
    groups: dict[str, dict] = {}
    dropped = 0
    for c in snap["contracts"]:
        exp_ts = c.get("exp_ts")
        if exp_ts is None or exp_ts <= as_of or exp_ts <= fetched_at:
            dropped += 1
            continue
        g = groups.get(c["key"])
        if g is None:
            g = groups[c["key"]] = {"key": c["key"], "date": c["date"], "root": c["root"],
                                    "exp_ts": exp_ts, "rows": []}
        r = Row()
        r.k, r.cp, r.name = c["strike"], c["type"], c["name"]
        r.bid, r.ask, r.mark, r.iv = c.get("bid"), c.get("ask"), c.get("mark"), c.get("iv")
        r.oi, r.vol, r.f = c.get("oi"), c.get("volume"), c.get("fwd")
        r.delta, r.gamma, r.vega, r.theta = c.get("delta"), c.get("gamma"), c.get("vega"), c.get("theta")
        r.derived = False
        r.last, r.last_t = c.get("last"), c.get("last_t")
        r.bid_n, r.ask_n, r.mark_n = c.get("bid_native"), c.get("ask_native"), c.get("mark_native")
        g["rows"].append(r)

    unquoted = sorted(k for k, g in groups.items()
                      if not any(r.iv is not None or r.bid is not None or r.ask is not None or r.oi
                                 for r in g["rows"]))
    for k in unquoted:
        del groups[k]
    series = sorted(groups.values(), key=lambda s: (s["exp_ts"], s["root"]))
    # 1. the forward of every series
    for s in series:
        rows = s["rows"]
        rows.sort(key=lambda r: (r.k, r.cp))
        s["t"] = (s["exp_ts"] - as_of) / YEAR
        own = [r.f for r in rows if r.f]
        if own:
            s["fwd"], s["fwd_kind"] = _median(own), "source"
            s["fwd_src"] = "Deribit forward for this expiry (underlying_price)"
        else:
            fwd, at = _parity_forward(rows, spot)
            if fwd is not None:
                s["fwd"], s["fwd_kind"] = fwd, "parity"
                s["fwd_src"] = f"put-call parity at strike {at:g} (mid prices, undiscounted)"
            else:
                s["fwd"], s["fwd_kind"], s["fwd_src"] = None, "level", ""
    # 2. the underlying level the chain is priced at. Deribit: its index. CBOE: the published
    #    underlying price — unless the front expiry's parity forward disagrees with it (the cash index
    #    is closed while its options trade overnight, or the options price off a future, as VIX does):
    #    then the options' own level is the reference, and the page says so.
    level, level_kind, level_src, published = spot, "published", snap.get("spot_src"), None
    if is_cboe:
        front = next((s for s in series if s["fwd_kind"] == "parity" and s["t"] <= FRONT_MAX_T), None)
        if front is not None and (not spot or abs(front["fwd"] / spot - 1.0) > LEVEL_TOLERANCE):
            level, level_kind, published = front["fwd"], "implied", spot
            level_src = (f"forward of the {front['key']} expiry implied by put-call parity: the level these options "
                         f"are priced at. CBOE's underlying price ({spot:g}) is not that level right now "
                         "(cash index closed while its options trade, or options priced off a future)"
                         if spot else f"forward of the {front['key']} expiry implied by put-call parity "
                                      "(CBOE sent no underlying price)")
    # 3. greeks, ATM, totals
    n_derived = n_source = 0
    for s in series:
        rows, t = s["rows"], s["t"]
        if s["fwd"] is None:
            s["fwd"] = level
            s["fwd_src"] = "the underlying level (no strike has a two-sided call and put to imply a forward)"
        for r in rows:
            g = black76(r.f or s["fwd"], r.k, t, r.iv, r.cp) if r.iv is not None else None
            r.gm = g["gamma"] if g is not None else None
            if r.gamma is not None and r.delta is not None:
                n_source += 1
                continue
            if g is None:
                continue
            r.delta, r.gamma, r.vega, r.theta, r.derived = g["delta"], g["gamma"], g["vega"], g["theta"], True
            n_derived += 1
        smile = _smile(rows)
        s["atm_iv"] = _interp(smile, s["fwd"])
        ks = sorted({r.k for r in rows})
        s["atm_strike"] = min(ks, key=lambda k: abs(k - s["fwd"])) if (ks and s["fwd"]) else None
        s["n"] = len(rows)
        s["strikes"] = len(ks)
        s["oi"] = sum(r.oi or 0.0 for r in rows)
        s["volume"] = sum(r.vol or 0.0 for r in rows)
        s["c25"], s["p25"] = _delta_iv(rows, "C", 0.25), _delta_iv(rows, "P", -0.25)

    if is_cboe:
        native, mult, oi_unit = "USD", cboe_options.CONTRACT_SIZE, "contracts (x100)"
        price_note = "USD as quoted by CBOE; mark = bid/ask midpoint (shown only when both sides are quoted)"
        greeks = {"origin": "source", "model": "CBOE's own greeks (4 decimals); rows without a CBOE IV have none",
                  "units": "per 1 share / index unit: delta, gamma per $1, vega $ per vol point, theta $ per day"}
    else:
        native = snap.get("native") or "USD"
        base = snap["symbol"].split("_")[0]
        mult, oi_unit = 1.0, base
        if native == "USDC":
            price_note = "quoted in USDC by Deribit, shown as USD (1 USDC = 1 USD)"
        else:
            price_note = (f"Deribit quotes {base} options in {base}: USD = coin price x the expiry's forward "
                          "(underlying_price), the conversion Deribit's own pricing uses")
        greeks = {"origin": "derived",
                  "model": "Black-76 on each row's own mark IV and forward, r = 0 (Deribit's book summary has no greeks)",
                  "units": f"per 1 {base}: delta, gamma per $1, vega $ per vol point, theta $ per day"}
    return {
        "source": source, "symbol": snap["symbol"], "as_of": as_of, "fetched_at": fetched_at,
        "spot": level, "spot_kind": level_kind, "spot_src": level_src, "published": published,
        "native": native, "mult": mult,
        "oi_unit": oi_unit, "price_note": price_note, "greeks": greeks,
        "series": series, "by_key": {s["key"]: s for s in series},
        "n_contracts": sum(s["n"] for s in series), "n_derived": n_derived, "n_source": n_source,
        "n_iv": sum(1 for s in series for r in s["rows"] if r.iv is not None),
        "n_quoted": sum(1 for s in series for r in s["rows"] if r.bid is not None or r.ask is not None),
        "expired_dropped": dropped, "unquoted_series": unquoted, "_memo": {}, "_lock": threading.Lock(),
    }


def _chain(inst: dict) -> dict:
    """The cached chain of an instrument (one upstream call per TTL, single-flight, stale-on-error)."""
    opt = inst.get("options") or {}
    source, symbol = opt.get("source"), opt.get("symbol")
    if source == "deribit":
        currency, _, _ = deribit.market(symbol)

        def load():
            payload = deribit.fetch_summary(currency)
            chain = build_chain(deribit.parse_summary(payload, symbol))
            if not chain["series"]:
                raise UpstreamError("deribit", None, f"Deribit lists no {symbol} options right now")
            return chain
        return {**cached(f"deribit:options:{symbol}", DERIBIT_TTL, load, stale_ttl=600), "ttl": DERIBIT_TTL}
    if source == "cboe":
        idle = inst.get("cls") == "equity" and session.state("us_equity") != "open"
        ttl = CBOE_TTL_IDLE if idle else CBOE_TTL
        key = f"cboe:options:{symbol}"

        def load():
            payload = cboe_options.fetch_chain(symbol)
            chain = build_chain(cboe_options.parse_chain(payload, symbol, eastern_to_epoch=_eastern,
                                                         expiry_epoch=_expiry_epoch))
            if not chain["series"]:
                raise UpstreamError("cboe", None, f"the CBOE file for {symbol} holds no unexpired option")
            # Is this market quoting? CBOE rewrites some files (the indices, and stocks before the open)
            # with a fresh timestamp while every bid and ask in them is still the last session's. Compare
            # with the previous fetch: `quotes_moved_at` is the fetch at which a quote last differed.
            prev = cache.peek(key)
            old = prev.value if prev is not None and isinstance(prev.value, dict) else None
            chain["quotes_sig"] = _quote_signature(chain)
            if old is not None and old.get("quotes_sig") == chain["quotes_sig"]:
                chain["quotes_moved_at"], chain["quotes_seen"] = old.get("quotes_moved_at"), "unchanged"
            else:
                chain["quotes_moved_at"] = chain["fetched_at"]
                chain["quotes_seen"] = "first" if old is None else "moved"
            return chain
        return {**cached(key, ttl, load, stale_ttl=6 * 3600), "ttl": ttl}
    raise NoSource(f"no options source is wired for {inst['id']}")


def _quote_signature(chain: dict):
    """A fingerprint of every bid and ask of a chain (count, sum of bids, sum of asks)."""
    n, bids, asks = 0, 0.0, 0.0
    for s in chain["series"]:
        for r in s["rows"]:
            if r.bid is not None:
                bids += r.bid
                n += 1
            if r.ask is not None:
                asks += r.ask
                n += 1
    return (n, round(bids, 4), round(asks, 4))


def _memo(chain: dict, name: str, fn):
    """A view computed once per cached chain (the stale copy of a chain shares the memo)."""
    memo = chain["_memo"]
    if name in memo:
        return memo[name]
    with chain["_lock"]:
        if name not in memo:
            memo[name] = fn()
        return memo[name]


# ════════════════════════════════ payload helpers ════════════════════════════════
def _f(x, sig: int = 6):
    """A JSON-safe number rounded to `sig` significant digits; None for None / NaN / inf."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if x == 0:
        return 0.0
    return round(x, sig - 1 - int(math.floor(math.log10(abs(x)))))


def _head(inst: dict, chain: dict) -> dict:
    """Fields every payload of this slice carries. `as_of` is the SOURCE's time for these quotes
    (Deribit's clock; the moment CBOE wrote its file), so the age on the page is the data's age."""
    now = time.time()
    source = chain["source"]
    ttl = chain.get("ttl") or (DERIBIT_TTL if source == "deribit" else CBOE_TTL)
    if source == "deribit":
        fresh = "~RT"
        note = f"Deribit snapshot of the whole chain, refreshed every {DERIBIT_TTL} s while this page is open"
    else:
        fresh, note = _cboe_freshness(inst, chain)
    out = {
        "id": inst["id"], "source": source, "freshness": fresh, "delay": (inst.get("options") or {}).get("delay"),
        "as_of": chain["as_of"], "fetched_at": chain["fetched_at"], "server_time": now, "ttl": ttl,
        "spot": _f(chain["spot"], 8), "spot_kind": chain["spot_kind"], "spot_src": chain["spot_src"],
        "published_price": _f(chain["published"], 8), "currency": "USD", "note": note,
    }
    if not chain["n_iv"]:
        # seen every morning: CBOE's delayed file carries no bid, ask or IV for some minutes around the open
        out["blank"] = True
        out["note"] = ("The source holds no implied volatility for any contract right now"
                       + (" and no bid or ask" if not chain["n_quoted"] else "")
                       + (": CBOE empties its delayed file for some minutes before the first quotes of a session "
                          "arrive. Open interest is still listed" if source == "cboe" else "")
                       + ". " + note)
    if chain.get("stale"):
        out["stale"], out["error"] = True, chain.get("error")
    return out


def _cboe_freshness(inst: dict, chain: dict):
    """("D15" | "CLOSED", why). D15 = delayed quotes of a market that is trading; CLOSED = the last
    session's quotes. Decided from what CBOE sent, in this order:
      1. the file itself is old (CBOE stopped rewriting it)                      -> CLOSED
      2. no bid or ask changed between two fetches at least ten minutes apart    -> CLOSED
      3. a first look at a stock outside the US session (stock options do not
         trade then, though CBOE rewrites the file before the open)              -> CLOSED
      4. otherwise                                                               -> D15"""
    d15 = "CBOE delayed quotes: 15 minutes behind the market; open interest is the previous day's"
    last = "Quotes are the last session's; open interest is the previous day's"
    stamp = lambda t: time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(t))          # noqa: E731
    if chain["fetched_at"] - chain["as_of"] > CBOE_FRESH_S:
        return "CLOSED", f"US options session closed: CBOE last wrote this file at {stamp(chain['as_of'])}. {last}"
    seen, moved_at = chain.get("quotes_seen"), chain.get("quotes_moved_at")
    if seen == "unchanged" and moved_at is not None and chain["fetched_at"] - moved_at >= CBOE_QUIET_S:
        return "CLOSED", (f"These options are not trading: no bid or ask in CBOE's file has changed since "
                          f"{stamp(moved_at)}. {last}")
    closed = session.state("us_equity") != "open"
    if seen != "moved" and closed and inst.get("cls") == "equity":
        return "CLOSED", f"US options session closed (stock options trade 09:30-16:00 New York). {last}"
    if closed:
        return "D15", d15 + ". The US cash session is closed: these quotes move only while the options trade in extended hours"
    return "D15", d15


def _live(chain: dict, now: float):
    """Series that have not expired since the chain was cached."""
    return [s for s in chain["series"] if s["exp_ts"] > now]


def _dte(s: dict, now: float) -> float:
    return round((s["exp_ts"] - now) / 86400.0, 3)


def _expiry_row(s: dict, now: float, total_oi: float) -> dict:
    return {
        "key": s["key"], "date": s["date"], "root": s["root"], "exp_ts": s["exp_ts"], "dte": _dte(s, now),
        "n": s["n"], "strikes": s["strikes"], "oi": _f(s["oi"], 9),
        "oi_pct": _f(100.0 * s["oi"] / total_oi, 4) if total_oi else None,
        "volume": _f(s["volume"], 9), "atm_iv": _f(s["atm_iv"], 5), "atm_strike": s["atm_strike"],
        "fwd": _f(s["fwd"], 8), "fwd_src": s["fwd_src"],
    }


def _default_expiry(live: list, now: float):
    """The nearest expiry date at least 7 days out (the front dailies are degenerate hours before
    they expire) — of the roots that expire that day, the one with the most open interest. When
    nothing is that far out, the last one listed."""
    far = [s for s in live if s["exp_ts"] - now >= 7 * 86400]
    if not far:
        return live[-1] if live else None
    same_day = [s for s in far if s["date"] == far[0]["date"]]
    return max(same_day, key=lambda s: s["oi"])


def _require(id_: str):
    inst = universe.require_cap(id_, "options")
    chain = _chain(inst)
    now = time.time()
    live = _live(chain, now)
    if not live:
        raise UpstreamError(chain["source"], None, f"every listed {inst['id']} expiry has passed; waiting for the next refresh")
    return inst, chain, live, now


# ════════════════════════════════ routes ════════════════════════════════
@router.get("/api/options")
def options(id: str = Query(..., min_length=1, max_length=16, description="terminal id, e.g. BTC, SPX")):
    """Expiry list of an underlying: date, days to expiry, contracts, open interest, ATM IV, forward."""
    inst, chain, live, now = _require(id)
    total_oi = sum(s["oi"] for s in live)
    multi_root = len({s["root"] for s in live}) > 1
    return {
        **_head(inst, chain),
        "expiries": [_expiry_row(s, now, total_oi) for s in live],
        "default_expiry": _default_expiry(live, now)["key"],
        "multi_root": multi_root,
        "totals": {"expiries": len(live), "contracts": sum(s["n"] for s in live), "oi": _f(total_oi, 9),
                   "volume": _f(sum(s["volume"] for s in live), 9)},
        "oi_unit": chain["oi_unit"], "contract_size": chain["mult"],
        "greeks": chain["greeks"], "price_note": chain["price_note"],
        "skipped": {"expired_contracts": chain["expired_dropped"], "unquoted_series": chain["unquoted_series"]},
    }


def _row_out(r: Row, native: bool) -> dict:
    out = {
        "strike": r.k, "type": r.cp, "name": r.name,
        "bid": _f(r.bid), "ask": _f(r.ask), "mark": _f(r.mark), "iv": _f(r.iv, 5),
        "delta": _f(r.delta, 5), "gamma": _f(r.gamma, 5), "vega": _f(r.vega, 5), "theta": _f(r.theta, 5),
        "oi": _f(r.oi, 9), "volume": _f(r.vol, 9), "derived": bool(r.derived),
        "last": _f(r.last), "last_t": r.last_t,
    }
    if native:
        out["bid_native"], out["ask_native"], out["mark_native"] = _f(r.bid_n), _f(r.ask_n), _f(r.mark_n)
    return out


@router.get("/api/options/chain")
def options_chain(id: str = Query(..., min_length=1, max_length=16),
                  expiry: str = Query(None, max_length=24,
                                      description="a key of /api/options expiries (YYYY-MM-DD or YYYY-MM-DD.ROOT); "
                                                  "omitted = the default expiry")):
    """One expiry: calls and puts per strike with bid, ask, mark (USD), IV, greeks, OI, volume."""
    inst, chain, live, now = _require(id)
    if expiry is None or expiry == "":
        s = _default_expiry(live, now)
    else:
        if not _EXPIRY.match(expiry):
            raise BadRequest("expiry must be YYYY-MM-DD or YYYY-MM-DD.ROOT (a key from /api/options)")
        s = next((x for x in live if x["key"] == expiry), None)
        if s is None:
            same_day = [x["key"] for x in live if x["date"] == expiry]
            if same_day:
                raise BadRequest(f"{len(same_day)} series expire on {expiry}: ask for one of {', '.join(same_day)}")
            raise NoSource(f"{inst['id']} has no listed expiry {expiry} (it may have expired). "
                           f"Listed: {', '.join(x['key'] for x in live[:12])}{' …' if len(live) > 12 else ''}",
                           source=chain["source"])
    native = chain["native"] != "USD"
    total_oi = sum(x["oi"] for x in live)
    return {
        **_head(inst, chain),
        "expiry": _expiry_row(s, now, total_oi),
        "fwd": _f(s["fwd"], 8), "atm_strike": s["atm_strike"], "atm_iv": _f(s["atm_iv"], 5),
        "rows": [_row_out(r, native) for r in s["rows"]],
        "native": chain["native"], "oi_unit": chain["oi_unit"], "contract_size": chain["mult"],
        "greeks": chain["greeks"], "price_note": chain["price_note"],
    }


# ---------------------------------------------------------------- vol surface
_STEPS = (0.01, 0.02, 0.025, 0.05, 0.1, 0.25)


def _moneyness_grid(live: list) -> list:
    """Strike / forward columns of the surface, as wide as this underlying's volatility calls for:
    half-width w = 1.5 x the median ATM IV of its expiries (at least 0.20, at most 1.00), from
    1 - min(w, 0.5) to 1 + w, on the finest round step that needs at most 36 intervals. An index at
    15 % vol gets 0.80 … 1.20, BTC at 40 % gets 0.50 … 1.60, VIX options at 80 % get 0.50 … 2.00.
    1.00 is always a column."""
    atm = [s["atm_iv"] for s in live if s.get("atm_iv")]
    if not atm:
        return []
    w = max(0.20, min(1.00, 1.5 * _median(atm)))
    lo, hi = 1.0 - min(w, 0.5), 1.0 + w
    step = next((st for st in _STEPS if (hi - lo) / st <= 36 + 1e-9), _STEPS[-1])
    a, b = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    return [round(i * step, 4) for i in range(a, b + 1)]


def _surface(chain: dict, live: list, now: float) -> dict:
    grid = _moneyness_grid(live)
    out_iv, exps = [], []
    for s in live:
        fwd = s["fwd"]
        # the out-of-the-money side is the liquid one: puts below the forward, calls above
        side: dict[float, float] = {}
        other: dict[float, float] = {}
        for r in s["rows"]:
            if r.iv is None:
                continue
            otm = (r.cp == "P") == (r.k < fwd) if fwd else True
            (side if otm else other).setdefault(r.k, r.iv)
        for k, v in other.items():
            side.setdefault(k, v)
        pts = sorted(side.items())
        out_iv.append([_f(_interp(pts, m * fwd, reach=0.0), 5) if fwd else None for m in grid])
        exps.append({
            "key": s["key"], "date": s["date"], "root": s["root"], "dte": _dte(s, now), "exp_ts": s["exp_ts"],
            "fwd": _f(s["fwd"], 8), "atm_iv": _f(s["atm_iv"], 5), "atm_strike": s["atm_strike"],
            "c25": _f(s["c25"], 5), "p25": _f(s["p25"], 5),
            "k_min": pts[0][0] if pts else None, "k_max": pts[-1][0] if pts else None,
        })
    return {"expiries": exps, "moneyness": grid, "iv": out_iv}


@router.get("/api/vol_surface")
def vol_surface(id: str = Query(..., min_length=1, max_length=16)):
    """Implied volatility by expiry and moneyness (strike / that expiry's forward), plus the ATM term
    structure and 25-delta wings. `iv[e][m]` is null where the expiry lists no strike on both sides
    of that moneyness (nothing is extrapolated)."""
    inst, chain, live, now = _require(id)
    surf = _memo(chain, "surface", lambda: _surface(chain, chain["series"], chain["fetched_at"]))
    keep = {s["key"] for s in live}
    idx = [i for i, e in enumerate(surf["expiries"]) if e["key"] in keep]
    by_key = {s["key"]: s for s in live}
    return {
        **_head(inst, chain),
        "expiries": [{**surf["expiries"][i], "dte": _dte(by_key[surf["expiries"][i]["key"]], now)} for i in idx],
        "moneyness": surf["moneyness"],
        "iv": [surf["iv"][i] for i in idx],
        "method": ("iv[e][m]: the IV at strike = m x forward of expiry e, linear between the two nearest listed "
                   "strikes (out-of-the-money side: puts below the forward, calls above); null outside the listed "
                   "strikes. atm_iv: the same at m = 1. c25 / p25: IV at the 25-delta call / put."),
        "greeks": chain["greeks"],
    }


# ---------------------------------------------------------------- gamma exposure
def _gex(chain: dict) -> dict:
    """Gamma exposure over ALL cached series. Returned numbers are unrounded; see `gex` for the shape.

    Every line uses the Black-76 gamma of the row's own IV and forward (`Row.gm`) and that forward as
    the price: gamma x OI x contract size x F^2 x 1 % / 1e6. This is so for CBOE too, although CBOE
    sends a gamma: it has 4 decimals, i.e. ONE significant digit for an index at 7,500 or 28,000
    (0.0003), and thousands of such lines are summed with opposite signs. The sum with CBOE's own
    gammas is returned next to it as a cross-check (`src_total`)."""
    mult = chain["mult"]
    level = chain["spot"]
    per_k: dict[float, list] = {}            # strike -> [call gex, put gex, call oi, put oi]
    by_exp = []
    oi_total = oi_used = 0.0
    src_total, src_rows = 0.0, 0
    prof = []                                # (a, b, c): see _profile_at
    for s in chain["series"]:
        call = put = s_oi = s_used = 0.0
        t = s["t"]
        for r in s["rows"]:
            oi = r.oi or 0.0
            if oi <= 0:
                continue
            oi_total += oi
            s_oi += oi
            sign = 1.0 if r.cp == "C" else -1.0
            if r.gamma is not None and not r.derived and level:
                src_total += sign * gex_usd_mm(r.gamma, oi * mult, level)
                src_rows += 1
            fwd = r.f or s["fwd"]
            if r.gm is None or not fwd:
                continue
            oi_used += oi
            s_used += oi
            g = gex_usd_mm(r.gm, oi * mult, fwd)
            cell = per_k.setdefault(r.k, [0.0, 0.0, 0.0, 0.0])
            if r.cp == "C":
                call += g
                cell[0] += g
                cell[2] += oi
            else:
                put -= g
                cell[1] -= g
                cell[3] += oi
            # the same line as a function of the underlying level (see _profile_at)
            vol = r.iv * math.sqrt(t)
            a = (math.log(fwd / r.k) + 0.5 * vol * vol) / vol
            if abs(a) - _PROFILE_LOG / vol > 8.0:        # never within 8 sd of the money on the whole grid
                continue
            prof.append((a, 1.0 / vol, sign * oi * mult * fwd / vol * 1e-8 / _SQRT_2PI))
        by_exp.append({"key": s["key"], "date": s["date"], "root": s["root"], "exp_ts": s["exp_ts"],
                       "call": call, "put": put, "net": call + put, "oi": s_oi, "oi_used": s_used})
    strikes = sorted(per_k)
    grid = [round(PROFILE_LO + i * PROFILE_STEP, 4)
            for i in range(int(round((PROFILE_HI - PROFILE_LO) / PROFILE_STEP)) + 1)]
    profile = [_profile_at(prof, m) for m in grid]
    flip_m = _flip(prof, grid, profile)
    call_wall = max(strikes, key=lambda k: per_k[k][0]) if strikes else None
    put_wall = min(strikes, key=lambda k: per_k[k][1]) if strikes else None
    return {
        "spot": level, "strikes": strikes,
        "call": [per_k[k][0] for k in strikes], "put": [per_k[k][1] for k in strikes],
        "call_oi": [per_k[k][2] for k in strikes], "put_oi": [per_k[k][3] for k in strikes],
        "by_expiry": by_exp, "oi_total": oi_total, "oi_used": oi_used,
        "grid": grid, "profile": profile, "flip_m": flip_m,
        "call_wall": call_wall if call_wall is not None and per_k[call_wall][0] > 0 else None,
        "put_wall": put_wall if put_wall is not None and per_k[put_wall][1] < 0 else None,
        "per_k": per_k, "n_profile": len(prof),
        "src_total": src_total if src_rows else None,
    }


_PROFILE_LOG = max(abs(math.log(PROFILE_LO)), abs(math.log(PROFILE_HI)))


def _profile_at(prof: list, m: float) -> float:
    """Total gamma exposure ($mm per 1 %) if the underlying — and with it every forward — stood at
    m x today's level and every contract kept its IV: sum of sign x OI x gamma(m F) x (m F)^2 x 1 % / 1e6.
    With d1(m) = a + ln(m) / vol and gamma x F^2 = pdf(d1) x F / vol, one line is m x c x exp(-d1^2 / 2)."""
    if not prof:
        return 0.0
    lm = math.log(m)
    exp = math.exp
    return m * sum(c * exp(-0.5 * (a + lm * b) ** 2) for a, b, c in prof)


def _flip(prof: list, grid: list, profile: list):
    """The zero crossing of the gamma profile nearest to today's level (m = 1), refined by bisection.
    None when the profile does not change sign on the grid."""
    best = None
    for i in range(len(grid) - 1):
        y0, y1 = profile[i], profile[i + 1]
        if y0 == 0.0 and y1 == 0.0:
            continue
        if (y0 <= 0.0 <= y1) or (y1 <= 0.0 <= y0):
            mid = 0.5 * (grid[i] + grid[i + 1])
            if best is None or abs(mid - 1.0) < abs(best[0] - 1.0):
                best = (mid, i)
    if best is None:
        return None
    lo, hi = grid[best[1]], grid[best[1] + 1]
    ylo = profile[best[1]]
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        ymid = _profile_at(prof, mid)
        if (ylo <= 0.0) == (ymid <= 0.0):
            lo, ylo = mid, ymid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@router.get("/api/gex")
def gex(id: str = Query(..., min_length=1, max_length=16)):
    """Dealer gamma exposure by strike over ALL listed expiries, with the by-expiry breakdown, the
    call / put walls, the zero-gamma (flip) level and the share of open interest that could be used."""
    inst, chain, live, now = _require(id)
    g = _memo(chain, "gex", lambda: _gex(chain))
    spot = g["spot"]
    net = [c + p for c, p in zip(g["call"], g["put"])]
    call_total, put_total = sum(g["call"]), sum(g["put"])
    total_oi = g["oi_total"]
    pk = g["per_k"]
    flip = g["flip_m"] * spot if (g["flip_m"] is not None and spot) else None
    return {
        **_head(inst, chain),
        "unit": GEX_UNIT,
        "strikes": g["strikes"], "gex": [_f(v, 5) for v in net],
        "call_gex": [_f(v, 5) for v in g["call"]], "put_gex": [_f(v, 5) for v in g["put"]],
        "call_oi": [_f(v, 9) for v in g["call_oi"]], "put_oi": [_f(v, 9) for v in g["put_oi"]],
        "by_expiry": [{"key": e["key"], "date": e["date"], "root": e["root"],
                       "dte": round((e["exp_ts"] - now) / 86400.0, 3),
                       "call": _f(e["call"], 5), "put": _f(e["put"], 5), "net": _f(e["net"], 5),
                       "oi": _f(e["oi"], 9), "oi_pct": _f(100.0 * e["oi"] / total_oi, 4) if total_oi else None}
                      for e in g["by_expiry"]],
        # no contract could be used (no IV anywhere): the totals are unknown, not zero
        "total": _f(call_total + put_total, 5) if g["oi_used"] else None,
        "call_total": _f(call_total, 5) if g["oi_used"] else None,
        "put_total": _f(put_total, 5) if g["oi_used"] else None,
        "flip": _f(flip, 7),
        "flip_pct": _f((g["flip_m"] - 1.0) * 100.0, 4) if g["flip_m"] is not None else None,
        "flip_note": ("underlying level at which the total exposure changes sign when every contract is re-priced "
                      "with Black-76 at its own IV (sticky strike)" if flip is not None else
                      f"the re-priced total does not change sign between {PROFILE_LO:.0%} and {PROFILE_HI:.0%} of the "
                      "current level"),
        "call_wall": ({"strike": g["call_wall"], "gex": _f(pk[g["call_wall"]][0], 5)}
                      if g["call_wall"] is not None else None),
        "put_wall": ({"strike": g["put_wall"], "gex": _f(pk[g["put_wall"]][1], 5)}
                     if g["put_wall"] is not None else None),
        "profile": {"spots": [_f(m * spot, 7) for m in g["grid"]] if (spot and g["oi_used"]) else [],
                    "pct": [round((m - 1.0) * 100.0, 2) for m in g["grid"]],
                    "gex": [_f(v, 5) for v in g["profile"]] if (spot and g["oi_used"]) else [],
                    "at_spot": _f(_profile_at_one(g), 5) if g["oi_used"] else None,
                    "note": "Black-76 re-pricing of every contract with open interest at its own IV; IV held per strike"},
        "expiries_used": sum(1 for e in g["by_expiry"] if e["oi_used"] > 0), "expiries_listed": len(g["by_expiry"]),
        "oi_total": _f(total_oi, 9), "oi_used": _f(g["oi_used"], 9),
        "oi_covered_pct": _f(100.0 * g["oi_used"] / total_oi, 4) if total_oi else None,
        "oi_unit": chain["oi_unit"], "contract_size": chain["mult"],
        "convention": "dealers assumed long calls (+) and short puts (-): an assumption, not a measurement",
        "formula": ("per strike: sum over all expiries of (call gamma x call OI - put gamma x put OI) x contract size "
                    "x F^2 x 1% / 1e6; gamma = Black-76 from each contract's own IV and forward F, r = 0"),
        "gamma_origin": "derived",
        "source_gamma_total": _f(g["src_total"], 5),
        "source_gamma_note": ("the same sum with CBOE's own gammas, which are rounded to 4 decimals (one significant "
                              "digit for a high-priced index): shown as a cross-check only"
                              if g["src_total"] is not None else None),
    }


def _profile_at_one(g: dict):
    grid = g["grid"]
    for m, v in zip(grid, g["profile"]):
        if abs(m - 1.0) < 1e-9:
            return v
    return None
