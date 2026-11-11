# OPEN TERMINAL

A Bloomberg-style trading terminal — original, clean-room software — replicating the
full module set from the reference videos, backed by **open financial data**.

Two parts:
- `web/openterm.html` — the UI. Runs as a self-contained **simulation** anywhere
  (the published artifact), and flips to **LIVE** automatically when served next to
  the backend.
- `backend/` — a FastAPI gateway + provider adapters feeding every module real data.

## Modules (all from the reference tour)

MON · CHRT · BOOK · HEAT · FOOT · OPTS · VOLS · GREEKS · GEX · WX · SAT · SHIP ·
SPLC · NEWS · ECO · TNT. Type a mnemonic in the command line or click the menu.

## Live mode

```bash
cd backend
pip install -r requirements.txt
export LSE_API_KEY=...  FRED_API_KEY=...  AISSTREAM_API_KEY=...   # optional
uvicorn app:app --reload --port 8080
# open the terminal (served by the gateway, so it goes LIVE):
#   http://localhost:8080/app/openterm.html
```

The page auto-connects to the gateway it's served from; the GO LIVE / DISCONNECT
control and backend-URL box are in the status bar. The published artifact can't reach
a backend (sandbox), so it stays SIM.

Verified live end to end: real crypto (BTC/ETH/SOL) from Binance+Deribit over one
merged WebSocket, real equities (AAPL/NVDA/TSLA/XOM) from LSE, a live Deribit options
chain + GEX (208 BTC contracts), and NOAA active storms — all rendering in the UI.

## Open-data sources

| Module / use | Provider | Key | Status |
|---|---|---|---|
| OPTS / VOLS / GREEKS / GEX (crypto) | Deribit | no | live |
| CHRT / BOOK / HEAT / FOOT / MON (crypto) | Binance public mirror | no | live |
| merged live stream (`/ws`) | Binance + Deribit WebSockets | no | live |
| WX storms | NOAA / National Hurricane Center | no | live |
| WX / marine weather (global) | Open-Meteo | no | live |
| NEWS factors | Kenneth French Data Library | no | live |
| NEWS headlines + tone | GDELT | no | live |
| filings / fundamentals | SEC EDGAR | no | live |
| SAT base tiles | NASA GIBS | no | live |
| macro (GDP, CPI, …) | World Bank | no | live |
| MON / CHRT / OPTS / ECO (stocks, FX, macro, bond yields) | London Strategic Edge (`lse-data`) | `LSE_API_KEY` | live |
| ECO / macro series | FRED | `FRED_API_KEY` | live |
| SHIP tankers (AIS) | AISStream.io | `AISSTREAM_API_KEY` | live |
| energy fundamentals (crude/gas stocks) | EIA | `EIA_API_KEY` | wired |
| crops | USDA NASS QuickStats | `USDA_API_KEY` | wired |
| rig count | Baker Hughes (workbook) | — | wired |
| SPLC trade flows | UN Comtrade | optional | wired |
| intl macro | IMF / Eurostat / OECD | no | wired |
| equity cross-check / fallback | Alpha Vantage / Twelve Data / Tiingo / Finnhub | one key | wired |
| SAT high-res | Copernicus Sentinel-2 | account | wired |
| backtest / grade | OpenQuant engine (`../engine`) | no | live |

"live" = verified returning real data this session; "wired" = adapter + endpoint
built, needs its key/account.

## Endpoints

`/api/health` lists what's live and which keys are present. Data: `/api/quote`,
`/api/candles`, `/api/book`, `/api/trades`, `/api/options`, `/api/gex`,
`/api/storms`, `/api/weather`, `/api/marine`, `/api/satellite`, `/api/sentinel`,
`/api/factors`, `/api/macro`, `/api/calendar`, `/api/eia`, `/api/usda`,
`/api/rigcount`, `/api/news`, `/api/filings`, `/api/worldbank`, `/api/imf`,
`/api/eurostat`, `/api/oecd`, `/api/trade`, `/api/equity_quote`, `/api/grade`.
Live stream: `ws://localhost:8080/ws` (merged Binance + Deribit).

## Efficiency

Prices stream over one server→browser WebSocket (push, not poll). Greeks are computed
locally from the chain's IV to avoid per-strike calls. Slow data (instruments, factors,
FRED, GIBS) should be TTL-cached; chart series downsampled (LTTB) before sending.

## Notes / limits

Free tiers are delayed or capped: AISStream best-effort (1–3 min lag); Deribit OI ~8h
delayed; LSE free tier caps downloads; equity L2 depth needs LSE's paid tick feed; LSE
FX candles use a different symbol format (stocks confirmed). IMF/Comtrade public
endpoints are finicky and may need their free keys. The simulation has none of these
limits.
