"""World Bank Open Data — annual macro indicators by country (owner: S6). Keyless.
e.g. NY.GDP.MKTP.CD GDP, FP.CPI.TOTL.ZG inflation, SL.UEM.TOTL.ZS unemployment.
"""
from __future__ import annotations

import re

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import num

SOURCE = "worldbank"
_COUNTRY = re.compile(r"^[A-Za-z]{2,3}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,5}(\.[A-Z0-9]{1,8}){1,6}$")


def check(country: str, code: str) -> None:
    if not _COUNTRY.match(country or ""):
        raise BadRequest("country must be a 2- or 3-letter code (US, DEU, WLD)")
    if not _CODE.match(code or ""):
        raise BadRequest("code must be a World Bank indicator id such as NY.GDP.MKTP.CD")


def fetch(country: str = "US", code: str = "NY.GDP.MKTP.CD", per_page: int = 70, timeout: float = 25):
    check(country, code)
    url = (f"https://api.worldbank.org/v2/country/{country.upper()}/indicator/{code}?"
           + http.qs({"format": "json", "per_page": max(1, min(int(per_page), 200))}))
    return http.get_json(url, timeout=timeout, source=SOURCE)


def parse(payload) -> dict:
    """{"indicator", "country", "updated", "rows": [{date, value}] ascending (years without a value dropped)}."""
    if not isinstance(payload, list) or len(payload) < 2:
        msg = ""
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            msg = str((payload[0].get("message") or [{}])[0].get("value", ""))[:120]
        raise UpstreamError(SOURCE, None, f"no data ({msg or 'unexpected reply shape'})")
    meta, rows = payload[0] if isinstance(payload[0], dict) else {}, payload[1] or []
    out = [{"date": str(x.get("date")), "value": num(x.get("value"))} for x in rows
           if isinstance(x, dict) and num(x.get("value")) is not None]
    out.sort(key=lambda r: r["date"])
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    return {"indicator": (first.get("indicator") or {}).get("value"), "country": (first.get("country") or {}).get("value"),
            "updated": meta.get("lastupdated"), "rows": out}


def indicator(country="US", code="NY.GDP.MKTP.CD", per_page=60):
    """Old signature: [{date, value}] newest first."""
    return list(reversed(parse(fetch(country, code, per_page))["rows"]))
