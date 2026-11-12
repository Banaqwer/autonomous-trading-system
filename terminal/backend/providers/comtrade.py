"""UN Comtrade — annual bilateral trade flows (owner: S6). Feeds the crude import partners
table of the supply-chain page.

Keyless public preview endpoint (verified 2026-11-12): one period per request, no
`partnerCode` parameter (the old code sent partnerCode=all and was rejected with HTTP 400);
`includeDesc=true` fills the partner names. With COMTRADE_API_KEY the subscription
endpoint is used and the key travels in the `Ocp-Apim-Subscription-Key` header.

The data is ANNUAL and lags: on 2026-11-12 the newest US year was 2025 (34 partner rows).
Values are customs values in USD; `netWgt` is kilograms. Row partnerCode 0 is "World".
"""
from __future__ import annotations

import os
import re

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import num

BASE = "https://comtradeapi.un.org"
SOURCE = "comtrade"
_CODE = re.compile(r"^\d{1,6}$")
_YEAR = re.compile(r"^(19|20)\d\d$")


def fetch_flows(reporter: str = "842", commodity: str = "2709", year: str = "2024",
                flow: str = "M", timeout: float = 30) -> dict:
    """Raw payload: every partner of `reporter` for one HS code, one year, one flow."""
    reporter, commodity, year, flow = str(reporter), str(commodity), str(year), str(flow).upper()
    if not _CODE.match(reporter) or not _CODE.match(commodity):
        raise BadRequest("reporter and commodity must be numeric codes (842 = USA, 2709 = crude petroleum)")
    if not _YEAR.match(year):
        raise BadRequest("year must be a 4-digit year")
    if flow not in ("M", "X"):
        raise BadRequest("flow must be M (imports) or X (exports)")
    params = http.qs({"reporterCode": reporter, "cmdCode": commodity, "flowCode": flow,
                      "period": year, "includeDesc": "true"})
    key = os.environ.get("COMTRADE_API_KEY")
    if key:
        return http.get_json(f"{BASE}/data/v1/get/C/A/HS?{params}",
                             {"Ocp-Apim-Subscription-Key": key}, timeout, source=SOURCE)
    return http.get_json(f"{BASE}/public/v1/preview/C/A/HS?{params}", timeout=timeout, source=SOURCE)


def parse_flows(payload) -> dict:
    """{"year", "total": {value_usd, kg} | None, "rows": [{code, iso, name, value_usd, kg, share}]}
    — partners sorted by value, `share` = percent of the World row (None without one).
    Duplicate partner rows (several customs procedures / transport modes) keep the total row."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        msg = payload.get("error") if isinstance(payload, dict) else None
        raise UpstreamError(SOURCE, None, f"unexpected reply shape ({str(msg)[:120] or 'no data list'})")
    total, best, year = None, {}, None
    for r in payload["data"]:
        if not isinstance(r, dict):
            continue
        code, value = r.get("partnerCode"), num(r.get("primaryValue"))
        if code is None or value is None:
            continue
        # keep only the all-procedures / all-transport-modes / no-second-partner aggregate
        if (r.get("customsCode") or "C00") != "C00" or (r.get("motCode") or 0) != 0 or (r.get("partner2Code") or 0) != 0:
            continue
        year = year or r.get("refYear") or r.get("period")
        row = {"code": int(code), "iso": r.get("partnerISO"), "name": r.get("partnerDesc") or f"partner {code}",
               "value_usd": value, "kg": num(r.get("netWgt"))}
        if int(code) == 0:
            total = {"value_usd": value, "kg": row["kg"]}
        elif int(code) not in best or value > best[int(code)]["value_usd"]:
            best[int(code)] = row
    rows = sorted(best.values(), key=lambda x: -x["value_usd"])
    for row in rows:
        row["share"] = round(100.0 * row["value_usd"] / total["value_usd"], 2) if total and total["value_usd"] else None
    return {"year": int(year) if year is not None and str(year).isdigit() else None, "total": total, "rows": rows}


def flows(reporter="842", partner="all", commodity="2709", year="2023", flow="M"):
    """Old signature (kept for callers outside the gateway): [{partner, value_usd, qty, flow}].
    `partner` is ignored: the API returns every partner when none is named."""
    parsed = parse_flows(fetch_flows(reporter, commodity, year, flow))
    return [{"partner": r["name"], "value_usd": r["value_usd"], "qty": r["kg"],
             "flow": "Import" if str(flow).upper() == "M" else "Export"} for r in parsed["rows"]][:200]
