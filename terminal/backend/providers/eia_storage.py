"""EIA weekly crude-oil storage for the SAT module's storage figure (owner S5).

One small provider: the weekly ending stocks of crude oil at the hub a satellite site sits on
(Cushing, PADD 3 Gulf Coast, US total ex-SPR), from the EIA API v2 `seriesid` route. The key
travels in the X-Api-Key header (never in a URL). Independent of the S6 supply-chain provider.

EIA weekly crude-stock series (thousand barrels):
  Cushing, OK          PET.W_EPC0_SAX_YCUOK_MBBL.W
  PADD 3 (Gulf Coast)  PET.WCRSTP31.W
  US total, ex-SPR     PET.WCESTUS1.W
Recipe / key: terminal/audit/backend-sweep.json; EIA v2 docs eia.gov/opendata.
Python 3.9, stdlib + core only.
"""
from __future__ import annotations
import os
import re

from core import http
from core.errors import BadRequest, NotConfigured, UpstreamError
from core.util import num

SOURCE = "eia"
BASE = "https://api.eia.gov/v2"
SERIES_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,11}\.[A-Z0-9_\-]{2,48}\.[AMWDQH]$")

# hub key -> (EIA series id, human label, unit)
SERIES = {
    "cushing": ("PET.W_EPC0_SAX_YCUOK_MBBL.W", "Cushing, OK crude oil stocks", "kbbl"),
    "padd3": ("PET.WCESTP31.W", "Gulf Coast (PADD 3) crude oil stocks", "kbbl"),
    "us": ("PET.WCESTUS1.W", "US crude oil stocks (ex-SPR)", "kbbl"),
}


def configured() -> bool:
    return bool(os.environ.get("EIA_API_KEY"))


def _headers() -> dict:
    if not os.environ.get("EIA_API_KEY"):
        raise NotConfigured("EIA_API_KEY is not set — free at eia.gov/opendata/register.php",
                            source=SOURCE)
    return {"X-Api-Key": os.environ["EIA_API_KEY"]}


def fetch_series(series_id: str, length: int = 52, timeout: float = 20) -> dict:
    """Raw v2 `seriesid` payload (newest first, as the API sends it)."""
    if not SERIES_ID.match(series_id or ""):
        raise BadRequest("series_id must look like PET.WCESTUS1.W")
    length = max(1, min(int(length), 520))
    return http.get_json(f"{BASE}/seriesid/{series_id}?" + http.qs({"length": length}),
                         _headers(), timeout, source=SOURCE)


def parse_series(payload: dict) -> dict:
    """{unit, desc, obs:[{date, t, value}] ascending}. `value` in thousand barrels."""
    from core.util import date_epoch
    resp = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(resp, dict) or not isinstance(resp.get("data"), list):
        raise UpstreamError(SOURCE, None, "EIA reply has no response.data array")
    obs = {}
    unit = desc = None
    for r in resp["data"]:
        if not isinstance(r, dict):
            continue
        date = r.get("period")
        val = num(r.get("value"))
        if date is None or val is None:
            continue
        unit = unit or r.get("units")
        desc = desc or r.get("series-description")
        obs[date] = {"date": date, "t": date_epoch(date), "value": val}
    rows = sorted(obs.values(), key=lambda o: o["date"])
    if not rows:
        raise UpstreamError(SOURCE, None, "EIA reply had no usable observations")
    return {"unit": unit or "kbbl", "desc": desc, "obs": rows}


def storage(hub: str, length: int = 52) -> dict:
    """{hub, series_id, label, unit, desc, week, value, prev, chg, history:[{date,t,value}]}
    for a known hub key. Raises BadRequest for an unknown hub."""
    if hub not in SERIES:
        raise BadRequest("hub must be one of " + ", ".join(SERIES))
    series_id, label, unit0 = SERIES[hub]
    parsed = parse_series(fetch_series(series_id, length))
    obs = parsed["obs"]
    last = obs[-1]
    prev = obs[-2]["value"] if len(obs) >= 2 else None
    chg = (last["value"] - prev) if prev is not None else None
    return {"hub": hub, "series_id": series_id, "label": label,
            "unit": parsed["unit"] or unit0, "desc": parsed["desc"],
            "week": last["date"], "value": last["value"],
            "prev": prev, "chg": chg, "history": obs}
