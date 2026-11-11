"""The 24/7 alpha factory loop: Sense -> Reason -> Test -> Store -> Resolve -> Propose.

tick() runs one cycle; run_loop() runs it forever on a host. Causal/event analyses
become scored predictions (the accuracy tracker). High-confidence ones become
human-approval proposals — never auto live trades. seed_from_backtests() bootstraps
the accuracy board with real historical outcomes so it isn't empty on day one.
"""
from __future__ import annotations
import time
import store, signals, scanner, reasoner, datafeed

PROPOSE_CONF = 0.6     # only ideas this confident queue for human approval

_px = {}
def price(sym):
    if sym in _px and time.time() - _px[sym][0] < 120:
        return _px[sym][1]
    try:
        p = float(datafeed.get_bars(sym)["close"].iloc[-1]); _px[sym] = (time.time(), p); return p
    except Exception:
        return None

def _ingest(idea):
    """Turn a signal/hypothesis into a scored, time-stamped prediction (+ maybe a proposal)."""
    sym, direction = idea["instrument"], idea["direction"]
    if direction not in ("LONG", "SHORT"):
        return False
    if store.has_open(sym, direction, idea["source"]):
        return False
    px = price(sym)
    if not px:
        return False
    h = float(idea.get("horizon_days", 5)) or 5
    pid = store.add_prediction({"source": idea["source"], "domain": idea.get("domain", ""),
        "instrument": sym, "direction": direction, "entry_px": px, "horizon_days": h,
        "resolve_at": time.time() + h * 86400, "confidence": idea.get("confidence", 0),
        "thesis": idea.get("thesis", "")})
    if idea.get("confidence", 0) >= PROPOSE_CONF:
        store.add_proposal({"instrument": sym, "direction": direction, "qty": 1,
            "rationale": idea.get("thesis", ""), "confidence": idea.get("confidence", 0), "prediction_id": pid})
    return True

def tick():
    t0 = time.time()
    sigs = signals.generate().get("ideas", [])
    # normalize horizon_days from text horizons
    for s in sigs:
        s.setdefault("horizon_days", 5)
    snapshot = {"signals": sigs[:10], "time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    llm = reasoner.hypotheses(snapshot)
    new = 0
    for idea in sigs + llm:
        try:
            if _ingest(idea): new += 1
        except Exception:
            pass
    # resolve matured predictions
    due = store.open_due(); n_res = 0
    for d in due:
        px = price(d["instrument"])
        if px:
            store.resolve(d["id"], px); n_res += 1
    store.record_run(n_signals=len(sigs), n_edges=0, n_new=new, n_resolved=n_res, duration=round(time.time()-t0, 2))
    return {"new_predictions": new, "resolved": n_res, "signals": len(sigs), "llm_hypotheses": len(llm),
            "reasoning": "llm" if reasoner.enabled() else "rules"}

def seed_from_backtests(groups=("equities", "futures", "etf"), per=2):
    """Bootstrap the accuracy board from real historical closed trades."""
    seeded = 0
    syms = [s for g in groups for s in scanner.UNIVERSE.get(g, [])]
    for sym in syms:
        try:
            bars = scanner._bars(sym)
        except Exception:
            continue
        for key, make, desc in scanner.LENSES[:per]:
            try:
                res = scanner.Backtester().run(bars, make())
                rows = [r for r in res.trade_log.rows if str(r["Type"]).startswith("Exit")]
                for r in rows[-6:]:
                    pnl = float(r["Net P&L %"]) if r["Net P&L %"] not in ("", None) else 0
                    side = "LONG" if "long" in str(r["Type"]).lower() else "SHORT"
                    store.add_prediction({"source": "backtest:" + key, "domain": sym, "instrument": sym,
                        "direction": side, "entry_px": 100, "horizon_days": 0, "resolve_at": 0,
                        "confidence": 0.5, "thesis": f"{desc} historical trade", "status": "correct" if pnl > 0 else "wrong",
                        "backfilled": 1, "resolved_at": time.time(),
                        "resolved_px": 100 * (1 + (pnl/100 if side == "LONG" else -pnl/100)),
                        "pnl_pct": pnl, "correct": 1 if pnl > 0 else 0})
                    seeded += 1
            except Exception:
                pass
    return {"seeded": seeded}

def run_loop(interval_seconds=900):
    store.init_db()
    while True:
        try:
            print("tick:", tick())
        except Exception as e:
            print("tick error:", e)
        time.sleep(interval_seconds)

if __name__ == "__main__":
    import sys
    if "--seed" in sys.argv:
        print(seed_from_backtests())
    else:
        run_loop(int(sys.argv[1]) if len(sys.argv) > 1 else 900)
