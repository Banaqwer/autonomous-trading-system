#!/usr/bin/env python3
"""Make the alpha-factory track record portable across machines.

The accuracy history lives in quantpad/backend/factory.db, which is gitignored
(SQLite binary + runtime state). So on a fresh clone the track record is empty.
This tool exports that history to a committable JSON snapshot and restores it
anywhere.

    python3 scripts/track_record.py export     # factory.db  -> track_record.json
    python3 scripts/track_record.py import      # track_record.json -> factory.db
    python3 scripts/track_record.py status      # row counts + hit rate, no write

Commit track_record.json and the history travels with `git clone`. store.py also
auto-restores from it on first run when the DB is empty, so usually you only run
`export` (after a factory run) and commit the result.

Tables handled: predictions, proposals, runs. Snapshot preserves row ids so
proposal->prediction links stay valid. Import is INSERT OR REPLACE (idempotent).
stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BACKEND = os.path.join(REPO, "quantpad", "backend")
DEFAULT_DB = os.path.join(BACKEND, "factory.db")
DEFAULT_SNAPSHOT = os.path.join(BACKEND, "track_record.json")

TABLES = ("predictions", "proposals", "runs")  # whitelist — never trust names from JSON


def _conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def _table_cols(c, table):
    return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def _ensure_schema(db):
    """Create tables if the DB doesn't exist yet (mirrors store.init_db)."""
    c = _conn(db)
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
    c.commit()
    c.close()


def export(db, out):
    if not os.path.exists(db):
        print(f"no DB at {os.path.relpath(db, REPO)} — nothing to export.")
        return 1
    c = _conn(db)
    data = {"version": 1, "exported_at": time.time(), "source_db": os.path.basename(db), "tables": {}}
    counts = {}
    for t in TABLES:
        try:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
        except sqlite3.OperationalError:
            rows = []
        data["tables"][t] = rows
        counts[t] = len(rows)
    c.close()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    print(f"exported {os.path.relpath(out, REPO)}: " +
          ", ".join(f"{t}={counts[t]}" for t in TABLES))
    return 0


def restore(db, snap, replace=False):
    if not os.path.exists(snap):
        print(f"no snapshot at {os.path.relpath(snap, REPO)} — nothing to import.")
        return 1
    with open(snap, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _ensure_schema(db)
    c = _conn(db)
    total = 0
    for t in TABLES:
        rows = data.get("tables", {}).get(t, [])
        if not rows:
            continue
        valid = set(_table_cols(c, t))
        for r in rows:
            cols = [k for k in r if k in valid]  # drop any column not in the real schema
            ph = ",".join("?" * len(cols))
            c.execute(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({ph})",
                      tuple(r[k] for k in cols))
            total += 1
    c.commit()
    c.close()
    print(f"imported {total} rows into {os.path.relpath(db, REPO)} from "
          f"{os.path.relpath(snap, REPO)}.")
    return 0


def status(db):
    if not os.path.exists(db):
        print(f"no DB at {os.path.relpath(db, REPO)} (fresh — run `import` or start the backend).")
        return 0
    c = _conn(db)
    for t in TABLES:
        try:
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        print(f"  {t}: {n}")
    r = c.execute("SELECT COUNT(*) n, SUM(correct) c, AVG(pnl_pct) p "
                  "FROM predictions WHERE correct IS NOT NULL").fetchone()
    n, cc = r["n"] or 0, r["c"] or 0
    if n:
        print(f"  hit rate: {cc}/{n} = {cc/n:.1%}   avg pnl: {r['p']:.2f}%")
    c.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Portable alpha-factory track record.")
    ap.add_argument("cmd", choices=["export", "import", "status"])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_SNAPSHOT, help="export target")
    ap.add_argument("--in", dest="inp", default=DEFAULT_SNAPSHOT, help="import source")
    ap.add_argument("--replace", action="store_true")
    a = ap.parse_args()
    if a.cmd == "export":
        return export(a.db, a.out)
    if a.cmd == "import":
        return restore(a.db, a.inp, a.replace)
    return status(a.db)


if __name__ == "__main__":
    raise SystemExit(main())
