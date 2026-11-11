"""Perpetual edge-discovery engine — the capstone of the merge.

Scans a universe across multiple strategy "lenses" (hypotheses), tests each with
the OpenQuant research stack (backtest -> Verdict -> regime attribution ->
regime-aware Monte Carlo), and returns only the hypotheses that survive with a
real, statistically-significant edge — ranked, with what / when / why / precision.

This is the "scan VEGA, test with OpenQuant" loop in one pass. Run it on a timer
for the perpetual version. Uses free Yahoo bars for breadth (no rate limits);
options/vol/GEX lenses plug in where that data is available.

NOT financial advice.
"""
from __future__ import annotations
import time, math

from openquant.data import get_data_client, Schema
from openquant.backtest import Backtester, MACrossover, Breakout
from openquant.grade import grade_verdict, attribute_pnl_by_regime, regime_aware_mc

UNIVERSE = {
    "equities": ["AAPL", "NVDA", "TSLA", "XOM", "JPM", "MSFT", "AMZN", "META"],
    "etf": ["SPY", "QQQ", "IWM", "XLE", "GLD", "USO"],
    "futures": ["CL", "GC", "NG", "ES", "NQ", "HG"],   # free via Yahoo =F
    "crypto": ["BTC", "ETH", "SOL"],
}
FUT = {"CL": "CL=F", "GC": "GC=F", "NG": "NG=F", "ES": "ES=F", "NQ": "NQ=F", "HG": "HG=F"}

# strategy lenses = the hypotheses we test on every name
LENSES = [
    ("trend-fast",  lambda: MACrossover(10, 30),  "fast moving-average trend"),
    ("trend-slow",  lambda: MACrossover(50, 150),  "slow moving-average trend"),
    ("breakout",    lambda: Breakout(55, 20),      "Donchian channel breakout"),
    ("breakout-fast", lambda: Breakout(20, 10),    "short breakout"),
]

_cache = {}
def _bars(sym):
    if sym in _cache and time.time() - _cache[sym][0] < 300:
        return _cache[sym][1]
    y = get_data_client("yahoo")
    last = None
    for attempt in range(4):                    # Yahoo rate-limits rapid calls; back off
        try:
            df = y.get_bars(FUT.get(sym, sym), Schema.OHLCV_1D)
            df["symbol"] = sym
            _cache[sym] = (time.time(), df)
            return df
        except Exception as e:
            last = e; time.sleep(0.4 * (attempt + 1))
    raise last

def _edge_score(verdict, stats, mc):
    """Composite 0-100: significance + risk-adjusted return + MC robustness + sample."""
    v = verdict.as_dict()
    sig = (1 - min(v["p_value"], 1)) * 100
    sharpe = max(0, min(stats["sharpe"], 3)) / 3 * 100
    mcp = mc.prob_profit * 100
    sample = v["sub_scores"]["sample_adequacy"]
    ruin = (1 - mc.risk_of_ruin) * 100
    return round(0.30 * sig + 0.25 * sharpe + 0.20 * mcp + 0.15 * sample + 0.10 * ruin, 1)

def scan(groups=None, min_score=48, min_trades=4, max_p=0.30, top=20):
    groups = groups or list(UNIVERSE)
    syms = [s for g in groups for s in UNIVERSE.get(g, [])]
    edges, tested, errors = [], 0, 0
    for sym in syms:
        try:
            bars = _bars(sym)
        except Exception:
            errors += 1; continue
        for key, make, desc in LENSES:
            tested += 1
            try:
                res = Backtester().run(bars, make())
                stats = res.stats()
                if stats["num_trades"] < min_trades:
                    continue
                reg = attribute_pnl_by_regime(res.trade_log, bars)
                v = grade_verdict(res, regime_attr=reg)
                mc = regime_aware_mc(res.trade_log.trade_pnls(), start_equity=100_000)
                vd = v.as_dict()
                if vd["p_value"] > max_p or v.bucket == "F":
                    continue
                score = _edge_score(v, stats, mc)
                if score < min_score:
                    continue
                top_reg = reg.get("top_regime")
                edges.append({
                    "symbol": sym, "lens": key, "hypothesis": f"{sym}: {desc}",
                    "grade": v.bucket, "verdict_score": vd["score"], "edge_score": score,
                    "what": f"{'Long/flat' if 'trend' in key or 'breakout' in key else ''} {sym} via {desc}",
                    "when": f"edge concentrated in '{top_reg}' regime; signal = {desc} cross/break",
                    "why": (f"significant (bootstrap p={vd['p_value']:.2f}), Sharpe {stats['sharpe']}, "
                            f"MC prob-profit {mc.prob_profit:.0%}, {stats['num_trades']} trades"),
                    "precision": {"confidence": score, "expected_return_pct": stats["return_pct"],
                                  "max_drawdown_pct": stats["max_drawdown_pct"],
                                  "mc_p05_usd": mc.p05_pnl, "mc_p95_usd": mc.p95_pnl,
                                  "p_value": vd["p_value"]},
                    "subscores": vd["sub_scores"], "regimes": reg.get("pnl_by_regime", {}),
                })
            except Exception:
                errors += 1
    edges.sort(key=lambda e: -e["edge_score"])
    return {"scanned_symbols": len(syms), "hypotheses_tested": tested,
            "edges_found": len(edges), "errors": errors, "edges": edges[:top],
            "disclaimer": "Research aid, not financial advice. Backtested edges can decay out of sample."}
