# OpenQuant

An original, clean-room **reference implementation** of a quant-research platform
architecture — market data, cloud-style compute, backtesting, rigorous strategy
grading, and an AI research agent. Built to demonstrate how a product in this
category is put together end to end.

> This is independent software. It contains no code, assets, text, or branding
> from any commercial product, and is not affiliated with or endorsed by any.
> The architecture it mirrors was learned from publicly served web assets; the
> implementation here is written from scratch. For research/education — **not
> investment advice; trading involves risk of loss.**

## What actually works today

The **engine is fully functional and tested** against live market data (free
Yahoo adapter, no credentials needed):

- **Data layer** — a vendor-shaped interface (`get_bars`, schemas, datasets,
  symbology) with a free Yahoo adapter and an optional Databento adapter behind
  the same API.
- **Backtester** — event-driven, next-bar-open execution (no look-ahead), with
  commission + slippage, long/flat/short, a per-bar equity curve, and the
  canonical 15-column trade log.
- **Grading engine** — the "Verdict" (edge / robustness / risk / sample-adequacy,
  0–100 + letter bucket), bootstrap significance p-value and confidence
  intervals, market-regime detection and P&L attribution, regime-aware Monte
  Carlo, and a prop-firm evaluation simulator (pass rate, cost to funded, EV).
- **AI agent** — a Claude tool-use loop (`claude-opus-5-5`, adaptive thinking)
  wired to the engine as tools (pull data, backtest, grade, sweep, prop-firm,
  run Python in a sandbox), with Plan/Build modes. Runs with an Anthropic key;
  degrades cleanly without one.
- **Web app** (`web/`) — a Next.js 14 App Router skeleton mirroring the full
  product surface (routes, API handlers, Clerk auth, Stripe + PostHog seams).
  Scaffold-complete; wire credentials to run.

## Layout

```
openquant/
  engine/        Python: data + backtest + grading + agent (fully working)
    openquant/
      data/      vendor-shaped interface + Yahoo (free) & Databento adapters
      backtest/  engine, strategies, canonical trade log, parameter sweep
      grade/     verdict, regime, monte carlo, prop-firm
      agent/     Claude tool-use loop, tool registry, sandbox
      cli.py
    examples/demo.py
    tests/       pytest (offline, synthetic data)
  web/           Next.js 14 app skeleton (architecture mirror, seams marked)
```

## Quick start (engine)

```bash
cd engine
pip install -r requirements.txt
PYTHONPATH=. python examples/demo.py              # full pipeline on live data
PYTHONPATH=. python -m openquant.cli backtest SPY --fast 20 --slow 50 --prop-firm --csv /tmp/spy.csv
PYTHONPATH=. python -m openquant.cli sweep QQQ
PYTHONPATH=. python -m pytest tests/ -q
```

Run the AI agent (needs an Anthropic key):

```bash
cd engine && pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
PYTHONPATH=. python -m openquant.agent.agent "Grade a 20/50 MA crossover on SPY. Is it worth trading?"
```

Use institutional data instead of the free feed:

```bash
pip install databento
export DATABENTO_API_KEY=db-...  OPENQUANT_DATA=databento
```

## Web app

```bash
cd web && npm install && cp .env.example .env.local   # fill in Clerk/Stripe/PostHog
npm run dev
```

Every external-service plug-in point is marked with a greppable `// SEAM:`
comment.

## What "full production parity" additionally requires

This repo is the architecture and the hard, buildable core. A production service
on top of it needs paid accounts and ongoing operations that can't live in a
code repo: an institutional data vendor (e.g. Databento) for real futures/
options/tick data and order-book depth; a fleet of isolated compute sandboxes
(containers/microVMs) for the per-project Python machines; auth (Clerk),
billing (Stripe), analytics (PostHog), and object storage (R2/S3); and LLM API
keys for the agent. The seams for each are in place.

## License

MIT (your choice) — original work.
