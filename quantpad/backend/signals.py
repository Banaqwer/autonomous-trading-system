"""Event -> trade-idea engine. THE MERGE: fuses the VEGA-style world state (live
weather, news, macro) with the OpenQuant research brain to answer "what should I
trade, when, and why" as ranked, explainable ideas — each validatable in Quant Copilot.

Sources: NOAA/NHC storms, Open-Meteo weather, GDELT news tone, a macro event clock,
and a light momentum check from the engine's own bars.

NOT financial advice — a research aid. Every idea carries its evidence and a
confidence in [0,1] so you can see exactly why it fired.
"""
from __future__ import annotations
import math, time, datetime as dt

# providers live in the terminal backend (already on sys.path via app/datafeed)
try:
    from providers import nhc, openmeteo, gdelt
except Exception:
    nhc = openmeteo = gdelt = None
try:
    from providers import marketaux
except Exception:
    marketaux = None
try:
    from providers import eia as eia_provider
except Exception:
    eia_provider = None
try:
    from providers import firms, openfda, census
except Exception:
    firms = openfda = census = None
try:
    from providers import edgar
except Exception:
    edgar = None
try:
    from providers import laborcurrent, bls, fmp
except Exception:
    laborcurrent = bls = fmp = None
try:
    from providers import usda, dol, datagov, courtlistener
except Exception:
    usda = dol = datagov = courtlistener = None

import datafeed

GULF = (-92.0, 28.0)   # Gulf of Mexico oil infrastructure centroid

def _dist(lon, lat, ref=GULF):
    return math.hypot((lon - ref[0]) * math.cos(math.radians(lat)), lat - ref[1])

def _momentum(symbol, look=20):
    """Light trend confirmation from engine bars: % change over `look` bars."""
    try:
        df = datafeed.get_bars(symbol)
        c = df["close"]
        if len(c) > look:
            return float(c.iloc[-1] / c.iloc[-look] - 1) * 100
    except Exception:
        pass
    return None

def _idea(instrument, direction, thesis, trigger, horizon, confidence, evidence, source, validate=None):
    return {"id": f"{source}-{instrument}-{int(time.time()*1000)%100000}",
            "instrument": instrument, "direction": direction, "thesis": thesis,
            "trigger": trigger, "horizon": horizon, "confidence": round(max(0, min(1, confidence)), 2),
            "evidence": evidence, "source": source,
            "validate": validate or {"symbol": instrument}}

# ---- generators ----
def storm_energy():
    out = []
    if not nhc: return out
    try: storms = nhc.active_storms()
    except Exception: return out
    for s in storms:
        lon, lat = s.get("lon"), s.get("lat")
        if lon is None or lat is None: continue
        d = _dist(lon, lat)
        atlantic = -100 < lon < -10 and 5 < lat < 45
        kt = s.get("intensity_kt") or 0
        if not atlantic: continue                     # only basins that touch US energy supply
        proximity = max(0, 1 - d / 40)                # closer = higher
        conf = 0.35 + 0.4 * proximity + 0.25 * min(kt / 130, 1)
        mom = _momentum("CL")
        ev = [f"{s.get('classification','TS')} {s.get('name')} at {abs(lat):.1f}N {abs(lon):.1f}W, {kt:.0f} kt",
              f"{d:.1f}° from Gulf oil hub; moving {s.get('heading_deg','?')}°"]
        if mom is not None:
            ev.append(f"CL 20-bar momentum {mom:+.1f}% ({'confirms' if mom>0 else 'contradicts'} long)")
            conf += 0.05 if mom > 0 else -0.08
        out.append(_idea("CL", "LONG",
            f"Hurricane {s.get('name')} threatens Gulf oil export/refining; supply-shock risk not yet priced",
            "port closures / refinery shut-ins on approach or landfall",
            "2-10 days", conf, ev, "storm"))
        out.append(_idea("RB", "LONG",
            f"Refinery outages from {s.get('name')} tighten gasoline more than crude (crack widens)",
            "Gulf refinery shut-ins", "3-14 days", conf - 0.05, ev[:2], "storm"))
    return out

def weather_gas():
    out = []
    if not openmeteo: return out
    try: fc = openmeteo.forecast(41.88, -87.63, 48)   # Chicago proxy for heating demand
    except Exception: return out
    temps = [h["temp_c"] for h in fc if h.get("temp_c") is not None]
    if not temps: return out
    lo = min(temps)
    if lo < 2:                                         # cold snap -> heating demand
        conf = 0.45 + 0.3 * min((2 - lo) / 12, 1)
        mom = _momentum("NG")
        ev = [f"Forecast low {lo:.1f}°C in US heating belt over 48h", "cold snap lifts nat-gas heating demand"]
        if mom is not None: ev.append(f"NG 20-bar momentum {mom:+.1f}%")
        out.append(_idea("NG", "LONG", "Cold snap drives heating demand; nat-gas withdrawal risk",
                         "sustained sub-freezing forecast / storage draw", "1-7 days", conf, ev, "weather"))
    elif max(temps) > 32:
        out.append(_idea("NG", "LONG", "Heat drives power-burn demand for nat gas",
                         "heatwave / grid load", "1-7 days", 0.4, [f"Forecast high {max(temps):.1f}°C"], "weather"))
    return out

def news_tone():
    out = []
    if not gdelt: return out
    for q, sym, name in [("crude oil OR OPEC", "CL", "crude"), ("Federal Reserve rate", "US10Y", "rates")]:
        try: arts = gdelt.news(q, 30)
        except Exception: continue
        tones = [a["tone"] for a in arts if isinstance(a.get("tone"), (int, float))]
        if len(tones) < 5: continue
        avg = sum(tones) / len(tones)
        if abs(avg) < 1.2: continue
        direction = "LONG" if avg > 0 else "SHORT"
        conf = 0.3 + min(abs(avg) / 8, 0.35)
        out.append(_idea(sym, direction,
            f"News flow on {name} skews {'positive' if avg>0 else 'negative'} (GDELT tone {avg:+.1f})",
            "sentiment extreme / headline momentum", "hours-days", conf,
            [f"{len(tones)} articles, avg tone {avg:+.2f}", f"sample: {arts[0]['title'][:70]}" if arts else ""],
            "news"))
    return out

# cross-domain causal map: theme -> representative instrument + the causal chain.
# This is the oil-cyclone logic generalized: an event in a domain moves a market.
CAUSAL = [
    ("war OR armed conflict OR defense spending OR military buildup", "ITA", "defense", "conflict / higher defense budgets lift defense contractors"),
    ("semiconductor OR AI chips OR Nvidia OR TSMC OR chip export", "SMH", "semis", "chip-demand & supply headlines move semiconductors"),
    ("bank earnings OR credit stress OR net interest margin", "XLF", "banks", "credit & rate headlines move banks"),
    ("OPEC OR crude supply OR oil sanctions OR refinery outage", "USO", "oil", "supply/OPEC headlines move crude"),
    ("inflation OR gold demand OR safe haven OR real yields", "GLD", "gold", "inflation/haven demand moves gold"),
    ("airline demand OR jet fuel OR air travel", "JETS", "airlines", "travel-demand & fuel headlines move airlines"),
    ("recession OR Fed rate cut OR treasury yields OR bond", "TLT", "rates", "growth/rates headlines move long-duration bonds"),
    ("Bitcoin OR crypto ETF OR stablecoin regulation", "BTC", "crypto", "flow & regulation headlines move crypto"),
]
def causal_news():
    """Cross-domain news→market reasoning. Prefers Marketaux (per-entity sentiment);
    falls back to GDELT tone when Marketaux has no key."""
    out = []
    use_mx = marketaux is not None and marketaux.enabled()
    if not use_mx and not gdelt: return out
    for query, sym, theme, chain in CAUSAL:
        if use_mx:
            r = marketaux.theme_sentiment(query, 20)
            if not r or r["n"] < 3: continue
            avg, n, sample, src = r["avg"], r["n"], r["sample"], "Marketaux"   # avg in [-1,1]
            if abs(avg) < 0.12: continue
            conf = 0.30 + min(abs(avg), 0.5) * 0.6 + min(n / 40, 0.1)
        else:
            try: arts = gdelt.news(query, 25)
            except Exception:
                time.sleep(0.5); continue
            time.sleep(0.3)
            tones = [a["tone"] for a in arts if isinstance(a.get("tone"), (int, float))]
            if len(tones) < 6: continue
            avg, n = sum(tones) / len(tones) / 10.0, len(tones)              # normalize to ~[-1,1]
            sample = arts[0]["title"] if arts and arts[0].get("title") else ""
            src = "GDELT"
            if abs(avg) < 0.1: continue
            conf = 0.28 + min(abs(avg) * 3, 0.3) + min(n / 50, 0.12)
        direction = "LONG" if avg > 0 else "SHORT"
        ev = [f"{n} {theme} articles, avg sentiment {avg:+.2f} ({src})"]
        if sample: ev.append("headline: " + sample[:72])
        mom = _momentum(sym)
        if mom is not None:
            agree = (mom > 0) == (direction == "LONG")
            ev.append(f"{sym} 20-bar momentum {mom:+.1f}% ({'confirms' if agree else 'contradicts'})")
            conf += 0.05 if agree else -0.07
        out.append(_idea(sym, direction,
            f"{chain}; news flow skews {'positive' if avg>0 else 'negative'}",
            "headline momentum / sentiment extreme", "hours-days", conf, ev, "causal:" + theme))
    return out

def eia_events():
    """Energy-inventory surprises (real fundamental): a weekly draw is bullish for the
    commodity, a build bearish. Uses the EIA key."""
    out = []
    import os
    if eia_provider is None or not os.environ.get("EIA_API_KEY"):
        return out
    for series_id, sym, label in [("PET.WCESTUS1.W", "CL", "crude inventories"),
                                   ("NG.NW2_EPG0_SWO_R48_BCF.W", "NG", "nat-gas storage")]:
        try: obs = eia_provider.series(series_id, 4)
        except Exception: continue
        vals = [o["value"] for o in obs if o.get("value") is not None]
        if len(vals) < 2: continue
        chg = vals[-1] - vals[-2]; base = abs(vals[-2]) or 1; pct = chg / base * 100
        if abs(pct) < 0.8: continue
        direction = "LONG" if chg < 0 else "SHORT"      # draw -> tighter supply -> long
        out.append(_idea(sym, direction,
            f"EIA {label} {'drew' if chg<0 else 'built'} {abs(chg):,.0f} w/w ({pct:+.1f}%) — "
            f"{'tightening' if chg<0 else 'loosening'} supply",
            "weekly EIA report", "1-5 days", 0.4 + min(abs(pct) / 5, 0.3),
            [f"latest {vals[-1]:,.0f} vs prior {vals[-2]:,.0f}", "EIA weekly series"], "eia:" + label))
    return out

_EDGAR_WATCH = {"AAPL","NVDA","TSLA","XOM","JPM","MSFT","AMZN","META","GOOGL","GOOG","JNJ","PFE",
                "TEVA","BAC","WFC","C","GS","XLE","SPY","QQQ","WMT","KO","DIS","INTC","AMD","MU","F","GM",
                "TGT","HD","LOW","NKE","COST","CRM","ORCL","AVGO","QCOM","BA","CAT","DE"}
# large employers whose WARN notices are tradable (name substring -> ticker)
_WARN_TICKER = {"intel": "INTC", "ford": "F", "general motors": "GM", "boeing": "BA", "amazon": "AMZN",
                "meta": "META", "microsoft": "MSFT", "nike": "NKE", "disney": "DIS", "paramount": "PARA",
                "ups": "UPS", "fedex": "FDX", "cisco": "CSCO", "dell": "DELL", "salesforce": "CRM",
                "citigroup": "C", "goldman": "GS", "wells fargo": "WFC", "tesla": "TSLA"}

_CONTRACTOR = {"lockheed": "LMT", "raytheon": "RTX", "rtx": "RTX", "boeing": "BA", "northrop": "NOC",
               "general dynamics": "GD", "l3harris": "LHX", "huntington ingalls": "HII",
               "leidos": "LDOS", "booz allen": "BAH", "palantir": "PLTR", "caci": "CACI"}

def usda_events():
    """Grain stocks -> corn/wheat futures (USDA NASS). A stocks build is bearish, a draw bullish."""
    out = []
    if usda is None:
        return out
    for commodity, sym in [("CORN", "ZC"), ("WHEAT", "ZW")]:
        try:
            rows = [r for r in usda.crop_stocks(commodity) if "US" in (r.get("geo") or "").upper() or (r.get("geo") or "").upper() in ("", "NATIONAL")]
        except Exception:
            rows = []
        vals = [r["value"] for r in rows if isinstance(r.get("value"), (int, float))]
        if len(vals) < 2:
            continue
        chg = vals[-1] / vals[-2] - 1 if vals[-2] else 0
        if abs(chg) < 0.05:
            continue
        direction = "SHORT" if chg > 0 else "LONG"      # stocks build -> bearish grain
        out.append(_idea(sym, direction,
            f"US {commodity.lower()} stocks {'built' if chg>0 else 'drew'} {abs(chg)*100:.0f}% q/q — {'looser' if chg>0 else 'tighter'} balance",
            "USDA NASS grain stocks", "weeks", 0.33 + min(abs(chg), 0.4) * 0.4,
            [f"{commodity} stocks {vals[-1]:,.0f} vs {vals[-2]:,.0f} (NASS)"], "usda:" + commodity.lower()))
    return out

def dol_events():
    """OSHA inspections with violations at a liquid employer -> operational/legal risk (SHORT)."""
    out = []
    if dol is None or not dol.enabled():
        return out
    for insp in dol.recent_inspections(limit=60, days=30):
        name = (insp.get("establishment") or "").lower()
        tk = next((t for k, t in _WARN_TICKER.items() if k in name), None)
        if not tk or not insp.get("violations"):
            continue
        out.append(_idea(tk, "SHORT",
            f"{insp.get('establishment')} drew {insp['violations']} OSHA violation(s) ({insp.get('state','')}) — safety/operational risk",
            "OSHA inspection with citations", "weeks", 0.38,
            [f"OSHA {insp.get('date','')} — {insp['violations']} violations (DOL)"], "dol:osha"))
        if len(out) >= 3:
            break
    return out

def datagov_events():
    """Large federal contract awards to a listed contractor -> revenue tailwind (LONG)."""
    out = []
    if datagov is None:
        return out
    for a in datagov.sam_awards(limit=25):
        name = (a.get("awardee") or "").lower()
        tk = next((t for k, t in _CONTRACTOR.items() if k in name), None)
        if not tk:
            continue
        out.append(_idea(tk, "LONG",
            f"{a.get('awardee')} won a federal contract ({a.get('agency','')}) — revenue tailwind",
            "SAM.gov contract award", "weeks", 0.40,
            [f"award: {(a.get('title') or '')[:60]}", f"amount {a.get('amount','n/a')} (SAM.gov)"], "datagov:sam"))
        if len(out) >= 3:
            break
    return out

def courtlistener_events():
    """Major litigation filed against a liquid name -> legal overhang (SHORT). Needs COURTLISTENER_API_TOKEN."""
    out = []
    if courtlistener is None or not getattr(courtlistener, "enabled", lambda: False)():
        return out
    for tk in list(_EDGAR_WATCH)[:8]:
        try:
            suits = courtlistener.recent_suits(query=tk, limit=3)
        except Exception:
            suits = []
        for s in suits[:1]:
            out.append(_idea(tk, "SHORT",
                f"New federal suit involving {tk}: {(s.get('case_name') or '')[:60]} — legal overhang",
                "RECAP docket filing", "weeks", 0.35,
                [f"{s.get('court','')} {s.get('date_filed','')} (CourtListener)"], "court:suit"))
    return out

def laborcurrent_events():
    """WARN (layoff) notices -> company stress. SHORT when a filing maps to a liquid ticker."""
    out = []
    if laborcurrent is None or not laborcurrent.enabled():
        return out
    for w in laborcurrent.recent_warn(30):
        name = (w.get("company") or "").lower()
        tk = next((t for k, t in _WARN_TICKER.items() if k in name), None)
        if not tk:
            continue
        emp = w.get("employees")
        out.append(_idea(tk, "SHORT",
            f"{w.get('company')} filed a WARN layoff notice ({emp or 'n/a'} employees, {w.get('state','')}) — operational stress",
            "WARN / mass-layoff filing", "days-weeks", 0.40,
            [f"WARN: {w.get('company')} {w.get('date','')}", "LaborCurrent"], "labor:warn"))
        if len(out) >= 4:
            break
    return out

def bls_events():
    """PPI acceleration -> input-cost / inflation pressure -> LONG gold (inflation hedge)."""
    out = []
    if bls is None:
        return out
    p = bls.series("WPUFD4")                 # PPI final demand
    if len(p) < 4:
        return out
    recent = (p[-1][1] / p[-4][1] - 1) * 100  # ~quarterly change
    if abs(recent) < 0.6:
        return out
    direction = "LONG" if recent > 0 else "SHORT"
    out.append(_idea("GLD", direction,
        f"PPI final demand {'accelerating' if recent>0 else 'cooling'} ({recent:+.1f}% q/q) — "
        f"{'inflation pressure supports' if recent>0 else 'disinflation pressures'} gold",
        "monthly BLS PPI release", "weeks", 0.35 + min(abs(recent) / 4, 0.25),
        [f"PPI {p[-1][0]} {p[-1][1]:.1f} vs {p[-4][0]} {p[-4][1]:.1f}", "BLS WPUFD4"], "bls:ppi"))
    return out

def earnings_events():
    """Upcoming earnings = scheduled binary event -> flag event risk on liquid names (FMP)."""
    out = []
    if fmp is None or not fmp.enabled():
        return out
    for e in fmp.earnings_calendar(7):
        sym = e.get("symbol")
        if not sym or sym not in _EDGAR_WATCH:
            continue
        out.append(_idea(sym, "HEDGE",
            f"{sym} reports earnings {e.get('date')} — scheduled binary event; size down or use options",
            f"earnings {e.get('date')}", "intraday", 0.5,
            [f"consensus EPS {e.get('eps_est')}", "FMP earnings calendar"], "earnings:" + sym))
        if len(out) >= 5:
            break
    return out

def edgar_events():
    """Material corporate events: an 8-K filed by a liquid name = event risk to review (SEC EDGAR)."""
    out = []
    if edgar is None:
        return out
    tmap = edgar.cik_to_ticker()
    seen = set()
    for f in edgar.recent_8k(80):
        tk = tmap.get(f["cik"])
        if not tk or tk not in _EDGAR_WATCH or tk in seen:
            continue
        seen.add(tk)
        out.append(_idea(tk, "HEDGE",
            f"{f['company']} filed an 8-K (material event) — confirm the item before positioning",
            "SEC 8-K filing", "intraday-days", 0.42,
            [f"8-K filed by {f['company']} ({tk})", "material corporate event via SEC EDGAR"], "edgar:8-K"))
        if len(out) >= 6:
            break
    return out

def firms_events():
    """Wildfire thermal anomalies -> timber supply & utility/insurer risk (NASA FIRMS)."""
    out = []
    if firms is None or not firms.enabled():
        return out
    n = firms.count("us_west", days=2)
    if n < 150:
        return out
    conf = 0.30 + min(n / 1500, 0.30)
    out.append(_idea("WOOD", "LONG",
        f"Elevated wildfire activity in the US West ({n} detections/48h) threatens standing timber and mills",
        "fire count surge near timber/utility regions", "1-10 days", conf,
        [f"{n} VIIRS fire detections, US West 48h (NASA FIRMS)", "also: utility liability (PCG) & insurer risk"], "firms:wildfire"))
    return out

def openfda_events():
    """Drug shortages / recalls -> pharma supply dynamics (openFDA, keyless)."""
    out = []
    if openfda is None:
        return out
    sh = openfda.drug_shortages(100)
    current = [s for s in sh if (s.get("status") or "").lower().startswith("current")]
    if len(current) >= 20:
        conf = 0.30 + min(len(current) / 200, 0.22)
        names = ", ".join(x["name"] for x in current[:3] if x.get("name"))
        out.append(_idea("TEVA", "LONG",
            f"{len(current)} active US drug shortages — scarcity supports generic-drug pricing power",
            "rising count of active shortages", "days-weeks", conf,
            [f"{len(current)} current shortages (openFDA)", "e.g. " + names[:70]], "openfda:shortage"))
    return out

def census_events():
    """Trade-flow shifts -> commodity supply/demand (US Census intl trade)."""
    out = []
    if census is None or not census.enabled():
        return out
    for hs, sym, name, up_dir in [("2709", "CL", "crude petroleum", "SHORT"),   # import surge = supply build
                                  ("8542", "SMH", "semiconductors", "LONG")]:    # import surge = demand
        s = census.imports_hs(hs, 6)
        if len(s) < 2:
            continue
        chg = s[-1][1] / s[-2][1] - 1 if s[-2][1] else 0
        if abs(chg) < 0.08:
            continue
        direction = up_dir if chg > 0 else ("LONG" if up_dir == "SHORT" else "SHORT")
        out.append(_idea(sym, direction,
            f"US {name} imports {'rose' if chg>0 else 'fell'} {abs(chg)*100:.0f}% m/m — shifting {name} balance",
            "monthly Census trade release", "weeks", 0.32 + min(abs(chg), 0.4) * 0.4,
            [f"{name} imports {s[-1][1]:,.0f} vs {s[-2][1]:,.0f} (US Census)"], "census:" + name.split()[0]))
    return out

def macro_clock():
    out = []
    now = dt.datetime.utcnow()
    # EIA Weekly Petroleum Status Report: Wednesdays ~15:30 UTC (10:30 ET)
    if now.weekday() == 2 and now.hour < 16:
        out.append(_idea("CL", "HEDGE",
            "EIA crude inventories today — binary event risk; size down or use options",
            "EIA report 10:30 ET", "intraday", 0.5,
            ["Scheduled high-impact release", "crude gaps on surprise draws/builds"], "macro"))
    # FOMC-ish placeholder via weekday heuristic omitted; real calendar via LSE economic_calendar
    return out

def generate(limit=12):
    ideas = []
    for gen in (storm_energy, weather_gas, eia_events, firms_events, openfda_events,
                census_events, edgar_events, laborcurrent_events, bls_events, earnings_events,
                usda_events, dol_events, datagov_events, courtlistener_events,
                causal_news, news_tone, macro_clock):
        try: ideas += gen()
        except Exception: pass
    ideas.sort(key=lambda i: -i["confidence"])
    return {"generated_at": dt.datetime.utcnow().isoformat() + "Z",
            "disclaimer": "Research aid, not financial advice. Validate every idea in Quant Copilot.",
            "ideas": ideas[:limit]}
