# RESUME — picking OpenQuant back up on any machine

Everything in this repo is portable. The only things that **don't** travel with
git are your live API keys and local runtime state (DBs, accuracy history) — this
file is how you restore them in a couple of commands.

---

## 1. Two layers: the chat vs. the code

- **The Claude Code conversation** lives server-side on your account. Reopen it
  from any computer at <https://claude.ai/code> (or the Claude Code desktop app)
  signed into the same account — it's in your session list. A *new* chat starts
  blank; reopen the existing one to keep all context. Turning a computer off does
  nothing to it (the session runs in the cloud, not on your machine).
- **This repo** is the code. Clone it anywhere. The steps below rebuild the
  secrets + runtime around it.

---

## 2. Get the code

```bash
git clone https://github.com/Banaqwer/autonomous-trading-system.git openquant
cd openquant
```

> The repo is named `autonomous-trading-system` until you rename it (GitHub →
> Settings → rename to `openquant`). The clone command updates automatically.

---

## 3. Restore the API keys (the one thing not in git)

`terminal/backend/config.py` holds live keys and is **gitignored on purpose**, so
it is never in the clone. Restore it one of two ways:

**Option A — regenerate from your private keys file (recommended).**
Keep `keys.local.env` on your machine (you have a copy; it is gitignored). Then:

```bash
python3 scripts/bootstrap_config.py --from keys.local.env
```

This writes `terminal/backend/config.py` (mode 0600) with every key. Drop
`keys.local.env` in the repo root and you can just run
`python3 scripts/bootstrap_config.py` (it's the default source).

You can also point it straight at the key tracker:

```bash
python3 scripts/bootstrap_config.py --from API_KEYS_with_values_1.md --check   # report, no write
python3 scripts/bootstrap_config.py --from API_KEYS_with_values_1.md           # write
```

**Option B — drop in the config.py you already have.**
Copy your saved `config.py` to `terminal/backend/config.py`. Done.

**On a deployed host**, skip the file entirely — set the same names as real
environment variables (they override the file via `os.environ.setdefault`).

Verify without printing any secret:

```bash
python3 scripts/bootstrap_config.py --check          # lists the key names it sees
```

---

## 4. Install deps & run

```bash
# data gateway + terminal (serves the Bloomberg-style UI at /app/openterm.html)
pip install -r terminal/backend/requirements.txt
cd terminal/backend && uvicorn app:app --port 8080
# open http://localhost:8080/app/openterm.html  (set OT_API to http://localhost:8080)

# OpenQuant Copilot / alpha factory (separate service)
pip install -r quantpad/backend/requirements.txt
cd quantpad/backend && uvicorn app:app --port 8090
```

Engine sanity check:

```bash
cd engine && python -m pytest -q         # 8 tests
python examples/demo.py
```

---

## 5. What is NOT in git (and how to get it back)

| Missing on a fresh clone | How to restore |
|---|---|
| `terminal/backend/config.py` (live keys) | Section 3 above |
| `keys.local.env` (private key source) | Keep your own copy; it's gitignored |
| `factory.db` (live accuracy DB) | **Auto-restores** from the committed `quantpad/backend/track_record.json` on first run. After a factory run, persist new history with `python3 scripts/track_record.py export` then commit the JSON. |
| `*.db`, `store.json` (other runtime state) | Regenerated on first run. |
| The **old** repo contents (Hurst trading system + ~215 photos) | Still in git history at commit `92b4f82` until GitHub garbage-collects it: `git clone … old && cd old && git checkout 92b4f82` |

---

## 6. Keys that need you (not blockers)

- `SEC_USER_AGENT` — set it to your real `Name email`; SEC requires a genuine
  contact on every EDGAR request. The default is a placeholder.
- `EARTHDATA_TOKEN` — expires ~2027-01-10; regenerate on the Earthdata profile page.
- Dead/pending: AirNow (retired), CZDS (login uses your registered email), PJM,
  CourtListener, USPTO — see the comments at the bottom of `keys.local.env`.

Treat every key in `keys.local.env` as **exposed** (they went through chat) and
regenerate any you care about from the provider dashboards.
