"""The 23 instruments of the terminal (REBUILD_SPEC §3.2): names, units, decimals,
capabilities, why-not texts, vendor symbols and quote source chains.

Instrument ids are the terminal ids (CL, EURUSD, BTC…) — never vendor symbols.
Everything else in the gateway asks this module:

    universe.get("CL")              -> instrument dict or None
    universe.require("CL")          -> instrument dict, or BadRequest (unknown id)
    universe.require_cap("CL", "book")   -> instrument, or NoSource with the why-not text
    universe.vendor("CL", "yahoo")  -> "CL=F"  (None when that vendor has no symbol)
    universe.ids("yahoo")           -> ids whose quote chain contains that source
    universe.payload()              -> the /api/universe body
"""
from __future__ import annotations

from core.errors import BadRequest, NoSource

GROUPS = [
    ("CMDTY", "Commodities", ["CL", "NG", "GC", "HG", "RB"]),
    ("FX", "FX", ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD"]),
    ("RATES", "Rates", ["US10Y", "US2Y", "DE10Y"]),
    ("INDEX", "Indices", ["SPX", "NDX", "VIX", "RUT"]),
    ("EQUITY", "Equities", ["AAPL", "NVDA", "TSLA", "XOM"]),
    ("CRYPTO", "Crypto", ["BTC", "ETH", "SOL"]),
]

# Freshness class of each (source, instrument class): RT real-time, D<n> delayed n min, EOD.
# "lse" is decided per quote from the age of the bar it returns (see core.quotes).
_NO = dict(book=False, trades=False, options=False)

_WHY_FUT = {
    "book": "Level-2 depth for CME futures needs a paid feed",
    "trades": "Time & sales for CME futures needs a paid feed",
}
_WHY_FX = {
    "book": "Spot FX is traded over the counter: there is no central order book and no free Level-2 feed",
    "trades": "Spot FX is traded over the counter: there is no free trade-by-trade feed",
    "options": "FX options are traded over the counter: no free source",
}
_WHY_EQ = {
    "book": "Level-2 depth for US equities needs a paid exchange feed",
    "trades": "A consolidated US equity tape needs a paid SIP feed",
}
_WHY_IDX = {
    "book": "An index is calculated, not traded: it has no order book",
    "trades": "An index is calculated, not traded: it has no trades",
}
_WHY_RATE = {
    "book": "A yield is a published rate, not an order book; bond depth needs a paid feed",
    "trades": "A yield is a published rate; bond trade prints need a paid feed",
    "options": "No free options source for government bond yields",
}
_EOD_NOTE = "End-of-day only: there is no free intraday source for this yield"


def _fut(id_, name, unit, dec, yahoo, lse_sym, what):
    return dict(id=id_, name=name, cls="future", unit=unit, decimals=dec, calendar="cme",
                chain=[("yahoo", "D10"), ("lse", "EOD")],
                vendor={"yahoo": yahoo, "lse": ("futures", lse_sym)},
                caps=dict(candles=True, **_NO), candles=["yahoo"],
                why={**_WHY_FUT, "options": f"No free options source for CME {what} futures"})


def _fx(id_, name, unit, dec):
    return dict(id=id_, name=name, cls="fx", unit=unit, decimals=dec, calendar="fx",
                # Yahoo FX is indicative and about a minute behind (measured 2026-11-12:
                # source time 16 s – 5 min old, median 61 s) -> D1, not RT
                chain=[("yahoo", "D1"), ("tiingo", "RT")],
                vendor={"yahoo": f"{id_}=X", "tiingo": id_.lower()},
                caps=dict(candles=True, **_NO), candles=["yahoo"], why=dict(_WHY_FX))


def _idx(id_, name, unit, yahoo, delay):
    return dict(id=id_, name=name, cls="index", unit=unit, decimals=2, calendar="us_equity",
                chain=[("yahoo", delay), ("cboe", "D15")],
                vendor={"yahoo": yahoo, "cboe": f"_{id_}"},
                caps=dict(candles=True, book=False, trades=False, options=True),
                candles=["yahoo"], why=dict(_WHY_IDX),
                options={"source": "cboe", "symbol": f"_{id_}", "delay": "D15"})


def _eq(id_, name):
    return dict(id=id_, name=name, cls="equity", unit="$", decimals=2, calendar="us_equity",
                chain=[("alpaca", "RT"), ("yahoo", "D15"), ("tiingo", "RT")],
                vendor={"alpaca": id_, "yahoo": id_, "tiingo": id_.lower(), "cboe": id_},
                caps=dict(candles=True, book=False, trades=False, options=True),
                candles=["alpaca", "yahoo"], why=dict(_WHY_EQ),
                options={"source": "cboe", "symbol": id_, "delay": "D15"})


def _crypto(id_, name, deribit):
    return dict(id=id_, name=name, cls="crypto", unit="USDT", decimals=2, calendar="crypto",
                chain=[("binance", "RT")],
                vendor={"binance": f"{id_}USDT", "deribit": deribit},
                caps=dict(candles=True, book=True, trades=True, options=True),
                candles=["binance"], why={},
                options={"source": "deribit", "symbol": deribit, "delay": "RT"})


_ALL = [
    _fut("CL", "WTI Crude (front month)", "$/bbl", 2, "CL=F", "CRUDEOIL", "crude"),
    _fut("NG", "Henry Hub Natural Gas (front month)", "$/MMBtu", 3, "NG=F", "NATGAS", "natural gas"),
    _fut("GC", "Gold (front month)", "$/oz", 1, "GC=F", "GOLD", "gold"),
    _fut("HG", "Copper (front month)", "$/lb", 4, "HG=F", "COPPER", "copper"),
    _fut("RB", "RBOB Gasoline (front month)", "$/gal", 4, "RB=F", "GASOLINE", "gasoline"),
    _fx("EURUSD", "Euro / US Dollar", "USD per EUR", 4),
    _fx("USDJPY", "US Dollar / Japanese Yen", "JPY per USD", 3),
    _fx("GBPUSD", "British Pound / US Dollar", "USD per GBP", 4),
    _fx("AUDUSD", "Australian Dollar / US Dollar", "USD per AUD", 4),
    dict(id="US10Y", name="US Treasury 10Y yield", cls="rate", unit="%", decimals=3,
         calendar="us_rates", chain=[("yahoo", "D15"), ("fred", "EOD")],
         vendor={"yahoo": "^TNX", "fred": "DGS10"},
         caps=dict(candles=True, **_NO), candles=["yahoo"], why=dict(_WHY_RATE)),
    dict(id="US2Y", name="US Treasury 2Y yield", cls="rate", unit="%", decimals=2,
         calendar="us_rates", chain=[("treasury", "EOD"), ("fred", "EOD")],
         vendor={"treasury": "2 Yr", "fred": "DGS2"},
         caps=dict(candles=True, **_NO), candles=["treasury"], candles_kind="line",
         why=dict(_WHY_RATE), note=_EOD_NOTE),
    dict(id="DE10Y", name="German Bund 10Y yield", cls="rate", unit="%", decimals=2,
         calendar="eu_rates", chain=[("bundesbank", "EOD")],
         vendor={"bundesbank": "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"},
         caps=dict(candles=True, **_NO), candles=["bundesbank"], candles_kind="line",
         why=dict(_WHY_RATE),
         note=_EOD_NOTE + " (Bundesbank fitted 10-year yield on listed Federal securities)"),
    _idx("SPX", "S&P 500", "pts", "^GSPC", "RT"),
    _idx("NDX", "Nasdaq 100", "pts", "^NDX", "RT"),
    _idx("VIX", "CBOE Volatility Index", "vol pts", "^VIX", "D15"),
    _idx("RUT", "Russell 2000", "pts", "^RUT", "D15"),
    _eq("AAPL", "Apple"),
    _eq("NVDA", "Nvidia"),
    _eq("TSLA", "Tesla"),
    _eq("XOM", "Exxon Mobil"),
    _crypto("BTC", "Bitcoin / USDT (Binance spot)", "BTC"),
    _crypto("ETH", "Ether / USDT (Binance spot)", "ETH"),
    _crypto("SOL", "Solana / USDT (Binance spot)", "SOL_USDC"),
]

# Rail labels: at most 12 characters, so nothing is cut off in the 280 px watchlist
# (the full name is in the header and in the row's tooltip).
SHORT = {"CL": "WTI Crude", "NG": "Nat Gas", "GC": "Gold", "HG": "Copper", "RB": "RBOB Gas",
         "EURUSD": "Euro", "USDJPY": "Yen", "GBPUSD": "Sterling", "AUDUSD": "Aussie",
         "US10Y": "UST 10Y", "US2Y": "UST 2Y", "DE10Y": "Bund 10Y",
         "SPX": "S&P 500", "NDX": "Nasdaq 100", "VIX": "VIX Index", "RUT": "Russell 2000",
         "AAPL": "Apple", "NVDA": "Nvidia", "TSLA": "Tesla", "XOM": "Exxon Mobil",
         "BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana"}
for _i in _ALL:
    _i["short"] = SHORT[_i["id"]]
    assert len(_i["short"]) <= 12, _i["id"]

INSTRUMENTS: dict[str, dict] = {i["id"]: i for i in _ALL}
ORDER: list[str] = [i for _, _, ids in GROUPS for i in ids]
CAPS = ("candles", "book", "trades", "options")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "1d", "1w")

for _gid, _gname, _ids in GROUPS:
    for _i in _ids:
        INSTRUMENTS[_i]["group"] = _gid

_BY_VENDOR: dict[tuple[str, str], str] = {}
for _inst in _ALL:
    for _src, _sym in _inst["vendor"].items():
        if isinstance(_sym, str):
            _BY_VENDOR[(_src, _sym.upper())] = _inst["id"]


def get(id_: str) -> dict | None:
    return INSTRUMENTS.get((id_ or "").strip().upper())


def require(id_: str) -> dict:
    inst = get(id_)
    if inst is None:
        raise BadRequest(f"unknown instrument id '{str(id_)[:24]}' — valid ids: {', '.join(ORDER)}")
    return inst


def has_cap(id_: str, cap: str) -> bool:
    inst = get(id_)
    return bool(inst and inst["caps"].get(cap))


def with_cap(cap: str) -> list[str]:
    return [i for i in ORDER if INSTRUMENTS[i]["caps"].get(cap)]


def require_cap(id_: str, cap: str) -> dict:
    """The instrument if it has the capability, else NoSource carrying the why-not
    text and the ids that do have it (REBUILD_SPEC rule 3)."""
    inst = require(id_)
    if not inst["caps"].get(cap):
        why = inst["why"].get(cap) or f"no {cap} source for {inst['id']}"
        raise NoSource(f"NO LIVE SOURCE — {why}. Available here for: {' · '.join(with_cap(cap))}")
    return inst


def vendor(id_: str, source: str):
    inst = get(id_)
    return inst["vendor"].get(source) if inst else None


def from_vendor(source: str, symbol: str) -> str | None:
    """'binance', 'BTCUSDT' -> 'BTC'."""
    return _BY_VENDOR.get((source, (symbol or "").upper()))


def ids(source: str | None = None, cls: str | None = None) -> list[str]:
    out = []
    for i in ORDER:
        inst = INSTRUMENTS[i]
        if source and source not in [s for s, _ in inst["chain"]]:
            continue
        if cls and inst["cls"] != cls:
            continue
        out.append(i)
    return out


def freshness(id_: str, source: str) -> str:
    inst = get(id_)
    for s, f in (inst["chain"] if inst else ()):
        if s == source:
            return f
    return "EOD"


def public(inst: dict) -> dict:
    out = {
        "id": inst["id"], "name": inst["name"], "short": inst["short"], "cls": inst["cls"],
        "group": inst["group"],
        "unit": inst["unit"], "decimals": inst["decimals"],
        "quote": {"sources": [s for s, _ in inst["chain"]], "delay": inst["chain"][0][1],
                  "delays": {s: f for s, f in inst["chain"]}},
        "caps": dict(inst["caps"]),
        "candles": {"sources": list(inst["candles"]), "kind": inst.get("candles_kind", "ohlc"),
                    "tfs": ["1d", "1w"] if inst.get("candles_kind") == "line" else list(TIMEFRAMES)},
        "why": dict(inst["why"]),
    }
    if inst.get("options"):
        out["options"] = dict(inst["options"])
    if inst.get("note"):
        out["note"] = inst["note"]
    return out


def payload() -> dict:
    return {
        "groups": [{"id": g, "name": n, "ids": list(ids_)} for g, n, ids_ in GROUPS],
        "instruments": {i: public(INSTRUMENTS[i]) for i in ORDER},
    }
