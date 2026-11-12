"""Released values ("actuals") for calendar events, from official series (owner: S6).

The calendar file has no `actual` column. For the main US prints the official number is
public minutes after the release: BLS (CPI, PPI, payrolls, unemployment rate, earnings),
FRED (weekly claims, retail sales, industrial production, housing, JOLTS, Fed funds
target) and EIA (crude inventories, gas storage). This module maps a calendar title to
its series and turns the series into the figure the calendar prints.

An actual is filled ONLY when
  1. the event time has passed, and
  2. the newest observation of the series is for the period THIS release reports
     (monthly: the month `lag` months before the event; weekly: the week ending the
     Saturday/Friday before the event; Fed funds: the day after the decision).
Otherwise the field stays None — an older observation is never shown as the new actual.
Every actual carries `actual_source` (who published it, which series), `actual_period`
and, when the figure was computed here from two observations, `derived: true`.
Non-US events have no free official source wired here: their actual stays None.
"""
from __future__ import annotations

import time

# title (currency USD) -> spec. kinds: bls_pct1 bls_pct12 bls_level_pct bls_net1_k | fred_level_k fred_mom
# fred_level_pct fred_level_m fred_next_day_pct | eia_chg_m eia_chg_b
SPECS = {
    "CPI m/m": {"src": "bls", "series": "CUSR0000SA0", "kind": "bls_pct1", "lag": 1},
    "Core CPI m/m": {"src": "bls", "series": "CUSR0000SA0L1E", "kind": "bls_pct1", "lag": 1},
    "CPI y/y": {"src": "bls", "series": "CUUR0000SA0", "kind": "bls_pct12", "lag": 1},
    "PPI m/m": {"src": "bls", "series": "WPSFD4", "kind": "bls_pct1", "lag": 1},
    "Core PPI m/m": {"src": "bls", "series": "WPSFD49104", "kind": "bls_pct1", "lag": 1},
    "Unemployment Rate": {"src": "bls", "series": "LNS14000000", "kind": "bls_level_pct", "lag": 1},
    "Non-Farm Employment Change": {"src": "bls", "series": "CES0000000001", "kind": "bls_net1_k", "lag": 1},
    "Average Hourly Earnings m/m": {"src": "bls", "series": "CES0500000003", "kind": "bls_pct1", "lag": 1},
    "Unemployment Claims": {"src": "fred", "series": "ICSA", "kind": "fred_level_k", "week_end": 5},      # Saturday
    "Retail Sales m/m": {"src": "fred", "series": "RSAFS", "kind": "fred_mom", "lag": 1},
    "Core Retail Sales m/m": {"src": "fred", "series": "RSFSXMV", "kind": "fred_mom", "lag": 1},
    "Industrial Production m/m": {"src": "fred", "series": "INDPRO", "kind": "fred_mom", "lag": 1},
    "Capacity Utilization Rate": {"src": "fred", "series": "TCU", "kind": "fred_level_pct", "lag": 1},
    "Building Permits": {"src": "fred", "series": "PERMIT", "kind": "fred_level_m", "lag": 1},
    "Housing Starts": {"src": "fred", "series": "HOUST", "kind": "fred_level_m", "lag": 1},
    "JOLTS Job Openings": {"src": "fred", "series": "JTSJOL", "kind": "fred_level_m", "lag": 2},
    "Federal Funds Rate": {"src": "fred", "series": "DFEDTARU", "kind": "fred_next_day_pct"},
    "Crude Oil Inventories": {"src": "eia", "series": "PET.WCESTUS1.W", "kind": "eia_chg_m", "week_end": 4},   # Friday
    "Natural Gas Storage": {"src": "eia", "series": "NG.NW2_EPG0_SWO_R48_BCF.W", "kind": "eia_chg_b", "week_end": 4},
}
SOURCE_LABEL = {"bls": "BLS", "fred": "FRED", "eia": "EIA"}
US_EASTERN_OFFSET = 5 * 3600          # close enough to pick the release's calendar date (EST/EDT differ by 1 h)


def spec_for(event: dict):
    if event.get("currency") != "USD":
        return None
    return SPECS.get(event.get("title"))


def _ymd(t: float):
    g = time.gmtime(t)
    return g.tm_year, g.tm_mon, g.tm_mday


def expected_period(spec: dict, event_t: float) -> str:
    """The observation date this release reports: 'YYYY-MM' (monthly) or 'YYYY-MM-DD'."""
    local = event_t - US_EASTERN_OFFSET
    if "lag" in spec:
        y, m, _ = _ymd(local)
        m -= spec["lag"]
        while m <= 0:
            y, m = y - 1, m + 12
        return f"{y:04d}-{m:02d}"
    day = int(local // 86400)
    if "week_end" in spec:                       # last such weekday strictly before the release date
        wd = (day + 3) % 7                       # Monday = 0 … Sunday = 6 (1970-01-01 was a Thursday)
        back = (wd - spec["week_end"]) % 7 or 7
        return "%04d-%02d-%02d" % _ymd((day - back) * 86400)
    return "%04d-%02d-%02d" % _ymd((day + 1) * 86400)      # fred_next_day_pct


def _fmt(v: float, d: int) -> str:
    s = f"{v:.{d}f}"
    return "0." + "0" * d if d and float(s) == 0 else s      # no "-0.0"


def compute(spec: dict, event_t: float, data) -> dict | None:
    """{"actual", "actual_period", "derived"} or None when the series does not (yet) hold the
    expected period. `data`: BLS rows newest first (providers.bls.parse_many()[series]), or
    FRED / EIA observations ascending ([{date, value}])."""
    want, kind = expected_period(spec, event_t), spec["kind"]
    if not data:
        return None
    if spec["src"] == "bls":
        row = next((r for r in data if r["period"] == want), None)
        if row is None or data[0]["period"] != want:
            return None
        if kind == "bls_pct1":
            v = row["pct"].get("1")
            return None if v is None else {"actual": _fmt(v, 1) + "%", "actual_period": want, "derived": False}
        if kind == "bls_pct12":
            v = row["pct"].get("12")
            return None if v is None else {"actual": _fmt(v, 1) + "%", "actual_period": want, "derived": False}
        if kind == "bls_level_pct":
            return {"actual": _fmt(row["value"], 1) + "%", "actual_period": want, "derived": False}
        if kind == "bls_net1_k":
            v = row["net"].get("1")
            return None if v is None else {"actual": _fmt(v, 0) + "K", "actual_period": want, "derived": False}
        return None
    obs = [o for o in data if o.get("value") is not None]
    if not obs:
        return None
    last = obs[-1]
    if not last["date"].startswith(want):
        return None
    prev = obs[-2] if len(obs) > 1 else None
    if kind == "fred_level_k":
        return {"actual": _fmt(last["value"] / 1000.0, 0) + "K", "actual_period": last["date"], "derived": False}
    if kind == "fred_level_pct":
        return {"actual": _fmt(last["value"], 1) + "%", "actual_period": want, "derived": False}
    if kind == "fred_next_day_pct":
        return {"actual": _fmt(last["value"], 2) + "%", "actual_period": last["date"], "derived": False}
    if kind == "fred_level_m":
        return {"actual": _fmt(last["value"] / 1000.0, 2) + "M", "actual_period": want, "derived": False}
    if prev is None or not prev["value"]:
        return None
    if kind == "fred_mom":
        return {"actual": _fmt((last["value"] / prev["value"] - 1.0) * 100.0, 1) + "%", "actual_period": want,
                "derived": True}
    if kind == "eia_chg_m":
        return {"actual": _fmt((last["value"] - prev["value"]) / 1000.0, 1) + "M", "actual_period": last["date"],
                "derived": True}
    if kind == "eia_chg_b":
        return {"actual": _fmt(last["value"] - prev["value"], 0) + "B", "actual_period": last["date"], "derived": True}
    return None
