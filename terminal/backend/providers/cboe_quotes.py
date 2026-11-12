"""CBOE delayed quotes (keyless, 15 minutes delayed) — index quote fallback (owner: FB).

  https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json   (_SPX _NDX _RUT, or a stock ticker)

After the close `prev_day_close` is overwritten with the day's close, so the previous
close is derived as current_price - price_change. `last_trade_time` is US Eastern
without an offset. (Option chains from the same CDN belong to providers/cboe_options.)
"""
from __future__ import annotations

from core import http
from core.util import iso_epoch, num

BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
SOURCE = "cboe"


def quote(symbol: str, timeout: float = 10) -> dict:
    return http.get_json(f"{BASE}/quotes/{symbol}.json", timeout=timeout, source=SOURCE)


def parse_quote(payload: dict, eastern_to_epoch=None) -> dict | None:
    """{last, prev_close, open, high, low, volume, ts, file_ts} or None if there is no price.

    `eastern_to_epoch(naive_iso)` converts the Eastern last_trade_time; without it (no
    time-zone data) the UTC timestamp of the quote file is used for `ts`."""
    d = (payload or {}).get("data") or {}
    last, chg = num(d.get("current_price")), num(d.get("price_change"))
    if not last:
        return None
    file_ts = iso_epoch((payload or {}).get("timestamp"))
    ts = eastern_to_epoch(d.get("last_trade_time")) if (eastern_to_epoch and d.get("last_trade_time")) else None
    prev = last - chg if chg is not None else None
    return {"last": last, "prev_close": round(prev, 6) if prev is not None else None,
            "open": num(d.get("open")) or None, "high": num(d.get("high")) or None,
            "low": num(d.get("low")) or None, "volume": num(d.get("volume")) or None,
            "bid": num(d.get("bid")) or None, "ask": num(d.get("ask")) or None,
            "ts": ts if ts is not None else file_ts, "file_ts": file_ts}
