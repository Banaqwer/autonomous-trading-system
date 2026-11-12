"""MACRO slice (S6): /api/news /api/calendar /api/supply/* + all reference routes.

Moved verbatim from the old app.py by the foundation; the slice owner rewrites it
against REBUILD_SPEC §3.7 (and fixes or retires the broken reference routes).
"""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException

from providers import (french, lse, fred, eia, edgar, gdelt, worldbank, comtrade,
                       intl_macro, usda, bakerhughes, equities_fallback, copernicus)

router = APIRouter()


def _need_key(fn):
    try:
        return fn()
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ---- NEWS / ECO / factors ----
@router.get("/api/factors")
def factors(dataset: str = "factors_monthly", limit: int = 360):
    return {"source": "ken-french", "dataset": dataset, "rows": french.factors(dataset, limit)}


@router.get("/api/macro")
def macro(series_id: str = "DCOILWTICO", limit: int = 120):
    return {"source": "fred", "series": series_id, "obs": _need_key(lambda: fred.series(series_id, limit))}


@router.get("/api/calendar")
def calendar(region: str = "US"):
    return {"source": "lse", "events": _need_key(lambda: lse.economic_calendar(region))}


# ---- energy / commodity fundamentals ----
@router.get("/api/eia")
def eia_series(series_id: str = "PET.WCESTUS1.W", length: int = 52):
    return {"source": "eia", "series": series_id, "obs": _need_key(lambda: eia.series(series_id, length))}


@router.get("/api/rigcount")
def rigcount():
    return {"source": "baker-hughes", **bakerhughes.rig_count()}


@router.get("/api/usda")
def usda_stats(commodity: str = "CORN", statistic: str = "PRODUCTION", year: str = "2024"):
    return {"source": "usda-nass", "rows": _need_key(lambda: usda.quickstats(commodity, statistic, year))}


# ---- news / filings ----
@router.get("/api/news")
def news(query: str = "crude oil OR OPEC", limit: int = 40):
    return {"source": "gdelt", "articles": gdelt.news(query, limit)}


@router.get("/api/filings")
def filings(q: str = "", forms: str = "8-K,10-Q,10-K"):
    return {"source": "sec-edgar", "filings": edgar.latest_filings(q, forms)}


# ---- international macro / trade (SPLC) ----
@router.get("/api/worldbank")
def wb(country: str = "US", code: str = "NY.GDP.MKTP.CD"):
    return {"source": "world-bank", "country": country, "code": code, "rows": worldbank.indicator(country, code)}


@router.get("/api/imf")
def imf(indicator: str = "NGDP_RPCH", country: str = "USA"):
    return {"source": "imf", "rows": intl_macro.imf(indicator, country)}


@router.get("/api/eurostat")
def eurostat(dataset: str = "prc_hicp_manr", params: str = "geo=EA&coicop=CP00"):
    return {"source": "eurostat", "rows": intl_macro.eurostat(dataset, params)}


@router.get("/api/oecd")
def oecd(flow: str = "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA"):
    return {"source": "oecd", **({"meta": intl_macro.oecd(flow)})}


@router.get("/api/trade")
def trade(reporter: str = "842", commodity: str = "2709", year: str = "2023", flow: str = "M"):
    return {"source": "un-comtrade", "flows": comtrade.flows(reporter, "all", commodity, year, flow)}


# ---- equity fallback / cross-check ----
@router.get("/api/equity_quote")
def equity_quote(symbol: str = "AAPL"):
    return _need_key(lambda: equities_fallback.quote(symbol))


# ---- higher-res satellite ----
@router.get("/api/sentinel")
def sentinel(layer: str = "TRUE-COLOR-S2L2A"):
    return {"source": "copernicus-sentinel2", **copernicus.wms_template(layer)}


# ---- engine: backtest / grade (reuses OpenQuant engine if installed) ----
@router.get("/api/grade")
def grade(symbol: str = "SPY", fast: int = 20, slow: int = 50):
    try:
        import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "engine"))
        from openquant.data import get_data_client, Schema
        from openquant.backtest import Backtester, MACrossover
        from openquant.grade import grade_verdict, attribute_pnl_by_regime
        bars = get_data_client().get_bars(symbol, Schema.OHLCV_1D)
        res = Backtester().run(bars, MACrossover(fast, slow))
        v = grade_verdict(res, regime_attr=attribute_pnl_by_regime(res.trade_log, bars))
        return {"source": "openquant-engine", "symbol": symbol, "stats": res.stats(), "verdict": v.as_dict()}
    except Exception as e:
        raise HTTPException(500, f"engine not available: {e}")
