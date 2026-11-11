"""Durable store for the alpha factory: predictions, their outcomes (the accuracy
tracker you asked for), and human-approval proposals. SQLite, stdlib only.
"""
from __future__ import annotations
import sqlite3, os, time, json

DB = os.path.join(os.path.dirname(__file__), "factory.db")

def _conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init_db():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS predictions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created REAL, source TEXT, domain TEXT, instrument TEXT, direction TEXT,
      entry_px REAL, horizon_days REAL, resolve_at REAL, confidence REAL, thesis TEXT,
      status TEXT DEFAULT 'open', resolved_at REAL, resolved_px REAL, pnl_pct REAL, correct INTEGER,
      backfilled INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS proposals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, instrument TEXT, direction TEXT,
      qty REAL, rationale TEXT, confidence REAL, status TEXT DEFAULT 'pending',
      decided_at REAL, broker_ref TEXT, prediction_id INTEGER);
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
      n_signals INTEGER, n_edges INTEGER, n_new INTEGER, n_resolved INTEGER, duration REAL);
    """)
    c.commit(); c.close()

# ---- predictions ----
def has_open(instrument, direction, source):
    c = _conn(); r = c.execute("SELECT 1 FROM predictions WHERE instrument=? AND direction=? AND source=? AND status='open' LIMIT 1",
                               (instrument, direction, source)).fetchone(); c.close(); return bool(r)

def add_prediction(p):
    c = _conn()
    c.execute("""INSERT INTO predictions(created,source,domain,instrument,direction,entry_px,horizon_days,resolve_at,confidence,thesis,status,backfilled,resolved_at,resolved_px,pnl_pct,correct)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (p.get("created", time.time()), p["source"], p.get("domain",""), p["instrument"], p["direction"],
               p["entry_px"], p["horizon_days"], p["resolve_at"], p.get("confidence",0), p.get("thesis",""),
               p.get("status","open"), p.get("backfilled",0), p.get("resolved_at"), p.get("resolved_px"),
               p.get("pnl_pct"), p.get("correct")))
    c.commit(); i = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.close(); return i

def open_due(now=None):
    now = now or time.time(); c = _conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM predictions WHERE status='open' AND resolve_at<=?", (now,))]
    c.close(); return rows

def resolve(pred_id, resolved_px):
    c = _conn(); r = c.execute("SELECT * FROM predictions WHERE id=?", (pred_id,)).fetchone()
    if not r: c.close(); return
    d = dict(r); sign = 1 if d["direction"] == "LONG" else (-1 if d["direction"] == "SHORT" else 0)
    pnl = sign * (resolved_px / d["entry_px"] - 1) * 100 if d["entry_px"] else 0
    correct = 1 if (sign != 0 and pnl > 0) else (0 if sign != 0 else None)
    status = "correct" if correct == 1 else ("wrong" if correct == 0 else "neutral")
    c.execute("UPDATE predictions SET status=?,resolved_at=?,resolved_px=?,pnl_pct=?,correct=? WHERE id=?",
              (status, time.time(), resolved_px, round(pnl, 3), correct, pred_id))
    c.commit(); c.close()

def recent(status=None, limit=100):
    c = _conn(); q = "SELECT * FROM predictions"; a = ()
    if status: q += " WHERE status=?"; a = (status,)
    q += " ORDER BY created DESC LIMIT ?"; a = a + (limit,)
    rows = [dict(r) for r in c.execute(q, a)]; c.close(); return rows

def accuracy():
    c = _conn()
    def hit(where="", args=()):
        r = c.execute(f"SELECT COUNT(*) n, SUM(correct) c, AVG(pnl_pct) p FROM predictions WHERE correct IS NOT NULL {where}", args).fetchone()
        n = r["n"] or 0; cc = r["c"] or 0
        return {"resolved": n, "correct": cc, "hit_rate": round(cc / n, 3) if n else None, "avg_pnl_pct": round(r["p"], 3) if r["p"] is not None else None}
    overall = hit()
    by_domain = {}
    for row in c.execute("SELECT DISTINCT domain FROM predictions WHERE correct IS NOT NULL AND domain!=''"):
        by_domain[row["domain"]] = hit("AND domain=?", (row["domain"],))
    by_source = {}
    for row in c.execute("SELECT DISTINCT source FROM predictions WHERE correct IS NOT NULL"):
        by_source[row["source"]] = hit("AND source=?", (row["source"],))
    openn = c.execute("SELECT COUNT(*) n FROM predictions WHERE status='open'").fetchone()["n"]
    c.close()
    return {"overall": overall, "by_domain": by_domain, "by_source": by_source, "open_predictions": openn}

# ---- proposals (human approval) ----
def add_proposal(p):
    c = _conn()
    c.execute("INSERT INTO proposals(created,instrument,direction,qty,rationale,confidence,prediction_id) VALUES(?,?,?,?,?,?,?)",
              (time.time(), p["instrument"], p["direction"], p.get("qty",1), p.get("rationale",""), p.get("confidence",0), p.get("prediction_id")))
    c.commit(); c.close()

def proposals(status="pending"):
    c = _conn(); rows = [dict(r) for r in c.execute("SELECT * FROM proposals WHERE status=? ORDER BY created DESC", (status,))]; c.close(); return rows

def decide(pid, decision, broker_ref=None):
    c = _conn(); c.execute("UPDATE proposals SET status=?,decided_at=?,broker_ref=? WHERE id=?",
                           (decision, time.time(), broker_ref, pid)); c.commit()
    row = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone(); c.close(); return dict(row) if row else None

def record_run(**k):
    c = _conn(); c.execute("INSERT INTO runs(ts,n_signals,n_edges,n_new,n_resolved,duration) VALUES(?,?,?,?,?,?)",
        (time.time(), k.get("n_signals",0), k.get("n_edges",0), k.get("n_new",0), k.get("n_resolved",0), k.get("duration",0)))
    c.commit(); c.close()

def last_run():
    c = _conn(); r = c.execute("SELECT * FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    n = c.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]; c.close()
    return {"last": dict(r) if r else None, "total_runs": n}

init_db()
