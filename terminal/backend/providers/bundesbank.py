"""Deutsche Bundesbank time-series API — German 10-year yield, end of day (owner: FB).

Keyless. Series BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A is the Bundesbank's
fitted (Svensson) 10-year yield on listed Federal securities — NOT a traded benchmark
quote, and END OF DAY ONLY. Weekend rows carry '.' and are skipped.
"""
from __future__ import annotations

import csv
import io

from core import http
from core.errors import UpstreamError
from core.util import date_epoch, num

BASE = "https://api.statistiken.bundesbank.de"
SERIES_10Y = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
SOURCE = "bundesbank"


def series_csv(series: str = SERIES_10Y, last_n: int = 12, timeout: float = 15) -> str:
    url = f"{BASE}/rest/data/{series}?lastNObservations={max(1, min(int(last_n), 4000))}"
    return http.get_text(url, {"Accept": "text/csv"}, timeout, source=SOURCE)


def parse_csv(text: str) -> list[dict]:
    """[{date, t, value}…] ascending (semicolon CSV with TIME_PERIOD / OBS_VALUE columns)."""
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")), delimiter=";")
    fields = reader.fieldnames or []
    if "TIME_PERIOD" not in fields or "OBS_VALUE" not in fields:
        raise UpstreamError(SOURCE, None, "unexpected CSV layout (no TIME_PERIOD / OBS_VALUE)")
    out = []
    for r in reader:
        value, t = num(r.get("OBS_VALUE")), date_epoch(r.get("TIME_PERIOD"))
        if value is None or t is None:
            continue
        out.append({"date": r["TIME_PERIOD"][:10], "t": int(t), "value": value})
    out.sort(key=lambda r: r["t"])
    return out
