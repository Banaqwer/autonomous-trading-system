"""OpenQuant — a clean-room clone of the QuantPad core product.

Projects + an AI research agent + the Quant Copilot grading surface (Verdict,
Performance, Risk & Monte Carlo, Trade Log, Regimes) + a Prop-Firm Desk, running
on open data (London Strategic Edge, Deribit, Binance, FRED, Kenneth French …) and
the OpenQuant engine. The web UI in ../web is served at /app.

Run:  pip install fastapi uvicorn lse-data
      uvicorn app:app --reload --port 8070
      open http://localhost:8070/app/index.html
"""
from __future__ import annotations
import os, sys, time, re, json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "terminal", "backend"))
try: import config  # baked keys
except Exception: pass

import datafeed
from openquant.backtest import Backtester, MACrossover, Breakout
from openquant.grade import (grade_verdict, attribute_pnl_by_regime,
                             regime_aware_mc, simulate_prop_firm, PropFirmSpec)

app = FastAPI(title="OpenQuant (QuantPad-style clone)", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STRATS = {"ma_crossover": MACrossover, "breakout": Breakout}
_bars_cache: dict = {}   # symbol -> (ts, df)  (60s TTL; also dodges LSE rate limits)

def bars(symbol, timeframe="1d"):
    k = (symbol.upper(), timeframe)
    hit = _bars_cache.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    df = datafeed.get_bars(symbol, timeframe)
    _bars_cache[k] = (time.time(), df)
    return df

def run(symbol, strategy="ma_crossover", fast=20, slow=50, equity=100_000.0):
    df = bars(symbol)
    factory = STRATS.get(strategy, MACrossover)
    strat = factory(fast, slow) if factory is MACrossover else factory(fast, slow)
    res = Backtester(initial_equity=equity).run(df, strat)
    return df, res

# ---------------- Quant Copilot: full 5-tab grading ----------------
@app.get("/api/copilot")
def copilot(symbol: str = "AAPL", strategy: str = "ma_crossover",
            fast: int = 20, slow: int = 50, equity: float = 100_000.0):
    try:
        df, res = run(symbol, strategy, fast, slow, equity)
    except Exception as e:
        raise HTTPException(502, f"data/grade failed for {symbol}: {e}")
    reg = attribute_pnl_by_regime(res.trade_log, df)
    verdict = grade_verdict(res, regime_attr=reg)
    mc = regime_aware_mc(res.trade_log.trade_pnls(), start_equity=equity)
    eq = res.equity
    curve = [[int(t.timestamp()), round(float(v), 2)] for t, v in eq.items()][::max(1, len(eq)//400)]
    return {
        "symbol": symbol.upper(), "strategy": strategy, "params": res.params,
        "verdict": verdict.as_dict(),
        "performance": res.stats(),
        "risk_montecarlo": mc.as_dict(),
        "regimes": reg,
        "trade_log": res.trade_log.to_frame().astype(str).to_dict("records"),
        "equity_curve": curve,
        "source": "lse" if os.environ.get("LSE_API_KEY") else "yahoo",
    }

@app.get("/api/propfirm")
def propfirm(symbol: str = "AAPL", strategy: str = "ma_crossover", fast: int = 20, slow: int = 50,
             account: float = 100_000, target: float = 8000, daily_loss: float = 4000, max_dd: float = 6000):
    _, res = run(symbol, strategy, fast, slow)
    spec = PropFirmSpec(account_size=account, profit_target=target,
                        daily_loss_limit=daily_loss, max_drawdown=max_dd)
    return {"symbol": symbol.upper(), **simulate_prop_firm(res, spec).as_dict()}

# ---------------- research agent / chat ----------------
def _parse(msg: str):
    """LLM-free intent parser so the agent works without a model key."""
    m = msg.upper()
    sym = None
    mm = re.search(r'\b([A-Z]{2,6}(?:USD|USDT)?)\b', m)
    cand = re.findall(r'\b[A-Z]{2,6}\b', m)
    STOP = {"THE","AND","FOR","RUN","GRADE","TEST","PROP","FIRM","MA","SWEEP","IS","IT","WORTH","TRADING","BACKTEST","ON","A","AN","GOAL","PLAN","BUILD"}
    for c in cand:
        if c not in STOP:
            sym = c; break
    fast, slow = 20, 50
    nums = re.findall(r'\b(\d{1,3})\b', m)
    if len(nums) >= 2: fast, slow = int(nums[0]), int(nums[1])
    intent = "propfirm" if "PROP" in m else ("sweep" if "SWEEP" in m else "grade")
    return intent, sym, fast, slow

@app.post("/api/chat")
def chat(body: dict):
    msg = (body or {}).get("message", "")
    # LLM path (if a key is present) via the OpenQuant agent
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            from openquant.agent.agent import run_agent
            return {"mode": "agent", "reply": run_agent(msg, verbose=False)}
        except Exception as e:
            pass
    # LLM-free: parse intent and drive the engine directly
    intent, sym, fast, slow = _parse(msg)
    if not sym:
        return {"mode": "scripted", "reply": "Name a symbol, e.g. 'grade AAPL 20 50' or 'prop-firm NVDA'.", "copilot": None}
    try:
        if intent == "propfirm":
            pf = propfirm(sym, "ma_crossover", fast, slow)
            return {"mode": "scripted", "reply": f"Prop-Firm Desk for {sym}: pass rate {pf['pass_rate']:.0%}, expected net ${pf['expected_net_cash']:,.0f}, call: {'GO' if pf['go'] else 'NO-GO'}.", "propfirm": pf}
        c = copilot(sym, "ma_crossover", fast, slow)
        v = c["verdict"]
        return {"mode": "scripted",
                "reply": f"Verdict for {sym} ({fast}/{slow} MA): grade {v['bucket']} ({v['score']}/100). "
                         f"Edge {v['sub_scores']['edge']}, robustness {v['sub_scores']['robustness']}, "
                         f"risk {v['sub_scores']['risk']}, sample {v['sub_scores']['sample_adequacy']}; "
                         f"bootstrap p={v['p_value']:.2f}. " + " ".join(v.get("notes", [])),
                "copilot": c}
    except HTTPException as e:
        return {"mode": "scripted", "reply": f"Could not analyse {sym}: {e.detail}", "copilot": None}

# ---------------- projects / memory / commands (simple JSON store) ----------------
_STORE = os.path.join(os.path.dirname(__file__), "store.json")
def _load(): 
    try: return json.load(open(_STORE))
    except Exception: return {"projects": [], "memory": [], "commands": _DEFAULT_CMDS}
def _save(d): json.dump(d, open(_STORE, "w"), indent=2)
_DEFAULT_CMDS = [
    {"name": "/grade", "hint": "<symbol> <fast> <slow>", "desc": "Backtest + Quant Copilot verdict"},
    {"name": "/propfirm", "hint": "<symbol>", "desc": "Price it against a prop-firm evaluation"},
    {"name": "/sweep", "hint": "<symbol>", "desc": "Parameter sweep over MA windows"},
    {"name": "/review", "hint": "", "desc": "Audit the current research for defects"},
]

@app.get("/api/projects")
def projects(): return {"projects": _load()["projects"]}
@app.post("/api/projects")
def add_project(body: dict):
    d = _load(); p = {"id": str(int(time.time())), "name": body.get("name", "Untitled"), "created": time.time()}
    d["projects"].append(p); _save(d); return p
@app.get("/api/memory")
def memory(): return {"memories": _load()["memory"]}
@app.post("/api/memory")
def add_memory(body: dict):
    d = _load(); mrec = {"id": str(int(time.time()*1000)), "content": body.get("content", ""), "scope": body.get("scope", "account")}
    d["memory"].append(mrec); _save(d); return mrec
@app.get("/api/commands")
def commands(): return {"commands": _load().get("commands", _DEFAULT_CMDS)}

_sig_cache = {}
@app.get("/api/signals")
def signals_api(limit: int = 12):
    import signals, time
    hit = _sig_cache.get(limit)
    if hit and time.time() - hit[0] < 180:          # cache so GDELT isn't hammered
        return {**hit[1], "cached": True}
    r = signals.generate(limit)
    _sig_cache[limit] = (time.time(), r)
    return r

_scan_cache = {}
@app.get("/api/scan")
def scan_api(groups: str = "equities,futures,etf", top: int = 20):
    import scanner, time
    key = (groups, top)
    hit = _scan_cache.get(key)
    if hit and time.time() - hit[0] < 600:            # 10-min cache (scan is heavy)
        return {**hit[1], "cached": True}
    r = scanner.scan(groups=[g.strip() for g in groups.split(",")], top=top)
    _scan_cache[key] = (time.time(), r)
    return r

# ---------------- 24/7 alpha factory ----------------
@app.post("/api/factory/tick")
def factory_tick():
    import orchestrator; return orchestrator.tick()

@app.post("/api/factory/seed")
def factory_seed():
    import orchestrator; return orchestrator.seed_from_backtests()

@app.get("/api/factory/accuracy")
def factory_accuracy():
    import store; return store.accuracy()

@app.get("/api/factory/status")
def factory_status():
    import store, reasoner, broker
    return {"runs": store.last_run(),
            "reasoning": "llm" if reasoner.enabled() else "rules (set ANTHROPIC_API_KEY)",
            "broker": "alpaca-paper" if broker.enabled() else "none (set APCA_API_KEY_ID/SECRET)",
            "loop_seconds": int(os.environ.get("OT_FACTORY_LOOP", "0"))}

@app.get("/api/predictions")
def predictions_api(status: str = None, limit: int = 100):
    import store; return {"predictions": store.recent(status, limit)}

@app.get("/api/proposals")
def proposals_api(status: str = "pending"):
    import store; return {"proposals": store.proposals(status)}

@app.post("/api/proposals/{pid}/decision")
def decide_api(pid: int, body: dict):
    import store, broker
    decision = (body or {}).get("decision", "reject")
    ref = None
    if decision == "approve":
        row = next((x for x in store.proposals("pending") if x["id"] == pid), None)
        if row:                                   # paper-trade only; never auto live
            side = "buy" if row["direction"] == "LONG" else "sell"
            ref = str(broker.place_paper(row["instrument"], side, row.get("qty", 1)))
    return {"decided": store.decide(pid, decision, ref), "broker": ref}

@app.on_event("startup")
def _factory_loop():
    secs = int(os.environ.get("OT_FACTORY_LOOP", "0"))
    if secs > 0:
        import threading, orchestrator
        threading.Thread(target=orchestrator.run_loop, args=(secs,), daemon=True).start()

@app.get("/api/health")
def health():
    return {"ok": True, "engine": True,
            "data": "lse" if os.environ.get("LSE_API_KEY") else "yahoo",
            "llm": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))}

_WEB = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.isdir(_WEB):
    @app.get("/")
    def _root(): return RedirectResponse("/app/index.html")
    app.mount("/app", StaticFiles(directory=_WEB, html=True), name="web")
