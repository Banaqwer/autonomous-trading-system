"""MACRO slice (S6): /api/news /api/calendar /api/supply/oil + the reference routes.

Rewritten against REBUILD_SPEC §3.7. Every upstream call goes through core.cache.cached
(single-flight, TTL, stale-on-error) and core.http; every parameter is validated or
clamped before it can reach an upstream URL; failures are typed errors.

    /api/supply/oil   EIA weekly petroleum balance (ONE upstream call for 19 series) + UN Comtrade
                      crude import partners (annual). WEEKLY data, labelled with the week-ending date.
    /api/news         Alpaca news + keyless RSS feeds + Marketaux (inside its 100/day budget) + SEC 8-K
                      filings of the terminal's equities; merged, de-duplicated, newest first, with a
                      per-provider status so the page can say which feed is down.
    /api/calendar     this week's economic calendar (ForexFactory export) with released values filled
                      from official series (BLS / FRED / EIA) where one exists — never invented.
    reference         /api/eia /api/macro (FRED) /api/trade (Comtrade) /api/filings (EDGAR)
                      /api/factors (Ken French) /api/worldbank /api/usda (NASS QuickStats)

Retired (no working free source, or superseded; see the slice report):
    /api/imf /api/eurostat /api/oecd /api/rigcount /api/equity_quote /api/sentinel /api/grade
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import time

from fastapi import APIRouter, Query

from core import universe
from core.cache import cached
from core.errors import ApiError, BadRequest, NoSource, RateLimited, describe
from providers import (actuals, bls, comtrade, diskstore, edgar, eia, forexfactory, fred, french,
                       marketaux, news as news_feeds, usda, worldbank)

router = APIRouter()
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="macro")


# =====================================================================================
# /api/supply/oil
# =====================================================================================
# key, EIA weekly series, label, stage, tight: the direction of change that TIGHTENS the crude
# balance (supportive for the crude price); None = no reading attached.
OIL_NODES = (
    ("production", "WCRFPUS2", "Field production", "supply", "down"),
    ("imports", "WCRIMUS2", "Crude imports", "supply", "down"),
    ("net_imports", "WCRNTUS2", "Net crude imports", "supply", "down"),
    ("stocks", "WCESTUS1", "Commercial crude stocks", "stocks", "down"),
    ("padd1", "WCESTP11", "PADD 1 East", "stocks", "down"),
    ("padd2", "WCESTP21", "PADD 2 Midwest", "stocks", "down"),
    ("padd3", "WCESTP31", "PADD 3 Gulf", "stocks", "down"),
    ("padd4", "WCESTP41", "PADD 4 Rockies", "stocks", "down"),
    ("padd5", "WCESTP51", "PADD 5 West", "stocks", "down"),
    ("cushing", "W_EPC0_SAX_YCUOK_MBBL", "Cushing, OK", "stocks", "down"),
    ("spr", "WCSSTUS1", "SPR (strategic reserve)", "stocks", None),
    ("refinery_inputs", "WCRRIUS2", "Refinery crude inputs", "refining", "up"),
    ("utilization", "WPULEUS3", "Refinery utilisation", "refining", "up"),
    ("exports", "WCREXUS2", "Crude exports", "refining", "up"),
    ("gasoline", "WGTSTUS1", "Gasoline stocks", "products", "down"),
    ("distillate", "WDISTUS1", "Distillate stocks", "products", "down"),
    ("jet", "WKJSTUS1", "Jet fuel stocks", "products", "down"),
    ("product_supplied", "WRPUPUS2", "Products supplied", "products", "up"),
)
OIL_TTL = 900                    # the weekly report lands on Wednesdays: a quarter of an hour is plenty
OIL_HISTORY_DAYS = 760           # two years of weeks for the history charts
PARTNERS_TTL = 24 * 3600
PARTNERS_STORE = "comtrade_us_crude_imports"


def _oil_load() -> dict:
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - OIL_HISTORY_DAYS * 86400))
    by = eia.parse_by_series(eia.fetch_weekly([n[1] for n in OIL_NODES], start))
    nodes = []
    for key, sid, label, stage, tight in OIL_NODES:
        s = by.get(sid)
        obs = s["obs"] if s else []
        node = {"key": key, "label": label, "stage": stage, "series": f"PET.{sid}.W",
                "desc": s["desc"] if s else None, "unit": s["unit"] if s else None, "tight": tight,
                "value": None, "prev": None, "chg": None, "chg_pct": None, "week": None, "week_t": None,
                "prev_week": None, "history": [[o["t"], o["value"]] for o in obs]}
        if obs:
            last = obs[-1]
            node.update(value=last["value"], week=last["date"], week_t=last["t"])
            if len(obs) > 1 and last["t"] - obs[-2]["t"] <= 8 * 86400:        # really the prior week
                prev = obs[-2]
                node.update(prev=prev["value"], prev_week=prev["date"], chg=round(last["value"] - prev["value"], 4),
                            chg_pct=round((last["value"] / prev["value"] - 1) * 100, 3) if prev["value"] else None)
        nodes.append(node)
    weeks = sorted({n["week"] for n in nodes if n["week"]})
    if not weeks:
        raise NoSource("EIA returned no weekly petroleum observations")
    return {"source": "eia", "freshness": "WEEKLY", "as_of": time.time(), "ttl": OIL_TTL, "week": weeks[-1],
            "week_t": max(n["week_t"] for n in nodes if n["week_t"]),
            "note": "EIA Weekly Petroleum Status Report: data for the week ending on the date shown, published "
                    "the following Wednesday 10:30 US Eastern (a day later after a federal holiday). "
                    "kbbl = thousand barrels, kb/d = thousand barrels a day.",
            "nodes": nodes}


def _partners_load() -> dict:
    """Crude (HS 2709) imports of the USA by partner for the newest year Comtrade has.
    Annual data: the answer is kept on disk for a day so a gateway restart does not ask again."""
    disk = diskstore.load(PARTNERS_STORE)
    if disk and isinstance(disk["value"], dict) and disk["value"].get("rows") and 0 <= time.time() - disk["at"] < PARTNERS_TTL:
        return disk["value"]
    year, last_error = time.gmtime(time.time()).tm_year - 1, None
    for y in (year, year - 1, year - 2):
        try:
            parsed = comtrade.parse_flows(comtrade.fetch_flows("842", "2709", str(y), "M"))
        except ApiError as e:
            last_error = e
            continue
        if parsed["rows"]:
            out = {"source": "un-comtrade", "freshness": "ANNUAL", "as_of": time.time(), "year": parsed["year"] or y,
                   "commodity": "HS 2709 crude petroleum oils", "reporter": "USA", "flow": "imports",
                   "total": parsed["total"], "rows": parsed["rows"][:40],
                   "note": "UN Comtrade annual customs statistics, reporter USA. Value in US dollars, weight in "
                           "kilograms as reported; share = part of the World total."}
            diskstore.save(PARTNERS_STORE, out)
            return out
    if last_error is not None:
        raise last_error
    raise NoSource("UN Comtrade has no US crude import rows for the last three years")


@router.get("/api/supply/oil")
def supply_oil():
    """US crude supply chain: one node per EIA weekly series + annual import partners."""
    out = dict(cached("eia:weekly-supply", OIL_TTL, _oil_load, stale_ttl=14 * 86400))
    try:
        out["partners"] = cached("comtrade:us-crude-imports", PARTNERS_TTL, _partners_load, stale_ttl=60 * 86400)
    except ApiError as e:
        out["partners"] = {"source": "un-comtrade", "error": describe(e)}
    return _readable(_readable(out), "partners")


# =====================================================================================
# /api/news
# =====================================================================================
NEWS_TTL = 300                   # Alpaca
RSS_TTL = 300
EDGAR_TTL = 900
MARKETAUX_TTL = 1800             # 48 calls a day at most with a page open all day (plan: 100)
MARKETAUX_RESERVE = 10           # never spend the last calls of the day
MARKETAUX_DAILY_CAP = 60         # our own ceiling, whatever the headers say
NEWS_WAIT = 8.0                  # a slow provider is reported as loading; it never holds the page
NEWS_MAX_AGE_DAYS = 14


_NOISE = re.compile(r"\s*[(\[]?\s*(?:window\.|document\.|function\s*\(|var\s|html\s*\{|body\s*\{|<!|<\w|\{\s*\")")


def _tidy(message) -> str:
    """An upstream's error page can drag script or style text into the message: keep the sentence."""
    text = " ".join(str(message or "").split())
    m = _NOISE.search(text)
    if m and m.start() >= 12:
        text = text[:m.start()]
    return text[:200].rstrip(" —-·:(")


def _why(err) -> str:
    """Readable reason from an ApiError or from the `error` dict of a stale cache payload."""
    if isinstance(err, BaseException):
        err = describe(err)
    return _tidy((err or {}).get("message"))


def _readable(out: dict, part: str = "") -> dict:
    """Copy of a payload (or of its `part`) whose error message is tidied; the cached object is not touched."""
    box = out.get(part) if part else out
    if isinstance(box, dict) and isinstance(box.get("error"), dict):
        box = {**box, "error": {**box["error"], "message": _tidy(box["error"].get("message"))}}
        return {**out, part: box} if part else box
    return out


def _alpaca_load(symbols=None) -> dict:
    return {"as_of": time.time(), "items": news_feeds.parse_alpaca(news_feeds.fetch_alpaca(symbols))}


def _rss_load(key: str) -> dict:
    return {"as_of": time.time(), "items": news_feeds.parse_rss(news_feeds.fetch_rss(key), key)}


def _utc_day(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if t is None else t))


def _marketaux_load() -> dict:
    disk = diskstore.load("marketaux_headlines")
    if disk and time.time() - disk["at"] < MARKETAUX_TTL and isinstance(disk["value"], dict):
        return disk["value"]                         # a restart must not cost a billed call
    budget = (diskstore.load("marketaux_budget") or {}).get("value") or {}
    if budget.get("day") != _utc_day():
        budget = {"day": _utc_day(), "calls": 0, "remaining": None}
    to_midnight = 86400 - time.time() % 86400
    if budget["calls"] >= MARKETAUX_DAILY_CAP or (budget.get("remaining") is not None
                                                 and budget["remaining"] <= MARKETAUX_RESERVE):
        raise RateLimited("marketaux", f"daily budget protected: {budget['calls']} calls made today, "
                          f"{budget.get('remaining')} left on the plan — resumes after 00:00 UTC",
                          retry_after=to_midnight)
    budget["calls"] += 1
    diskstore.save("marketaux_budget", budget)        # counted before the call: a failed call is billed too
    got = marketaux.fetch_headlines()
    if got["usage"].get("remaining") is not None:
        budget["remaining"] = got["usage"]["remaining"]
        diskstore.save("marketaux_budget", budget)
    out = {"as_of": time.time(), "items": marketaux.parse_headlines(got["payload"]), "usage": got["usage"]}
    diskstore.save("marketaux_headlines", out)
    return out


def _filing_items(rows) -> list:
    items = []
    for r in rows:
        what = ", ".join(f"{i['code']} {i['text']}" if i.get("text") else i["code"] for i in r["items"][:4])
        title = f"{r['company'] or 'CIK ' + r['cik']} filed {r['form'] or 'a filing'}" + (f": {what}" if what else "")
        items.append({"id": news_feeds.item_id("edgar", r["url"], title, r["t"]), "t": r["t"], "date_only": True,
                      "provider": "edgar", "source": "SEC EDGAR", "title": title[:news_feeds.MAX_TITLE], "url": r["url"],
                      "symbols": [r["ticker"]] if r.get("ticker") else [],
                      "summary": f"Form {r['form']} filed {r['filed']}"
                                 + (f", period ending {r['period']}" if r.get("period") else "")
                                 + ". EDGAR gives the filing date only (no time of day).",
                      "ids": [r["ticker"]] if r.get("ticker") in edgar.CIKS else [], "topics": ["filings", "equities"]})
    return items


def _edgar_load() -> dict:
    rows = edgar.parse_search(edgar.fetch_search("", ["8-K"], ciks=list(edgar.CIKS.values())))
    return {"as_of": time.time(), "items": _filing_items(rows)}


def _news_providers() -> list:
    """(name, label, kind, cache key, ttl, stale_ttl, loader)."""
    out = [("alpaca", "Alpaca / Benzinga", "alpaca", "alpaca-news:general", NEWS_TTL, 3600, _alpaca_load),
           ("alpaca-watch", "Alpaca watchlist", "alpaca", "alpaca-news:watch", NEWS_TTL, 3600,
            lambda: _alpaca_load(news_feeds.WATCH_SYMBOLS))]
    for key, feed in news_feeds.FEEDS.items():
        out.append((f"rss-{key}", feed["label"], "rss", f"rss-{key}:feed", RSS_TTL, 6 * 3600,
                    lambda key=key: _rss_load(key)))
    out.append(("marketaux", "Marketaux", "marketaux", "marketaux:headlines", MARKETAUX_TTL, 12 * 3600, _marketaux_load))
    out.append(("edgar", "SEC EDGAR 8-K", "edgar", "edgar:8k-universe", EDGAR_TTL, 24 * 3600, _edgar_load))
    return out


def _provider_off(name: str):
    """A provider that is not configured on this machine: the reason, else None."""
    if name.startswith("alpaca") and not news_feeds.alpaca_configured():
        return "APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set"
    if name == "marketaux":
        if os.environ.get("OT_MARKETAUX", "1").strip() == "0":
            return "switched off (OT_MARKETAUX=0)"
        if not marketaux.enabled():
            return "MARKETAUX_API_KEY is not set"
    return None


def _norm_url(url):
    if not url:
        return None
    m = re.match(r"^https?://(?:www\.)?([^/?#]+)([^?#]*)", url, re.I)
    return (m.group(1).lower() + m.group(2).rstrip("/").lower()) if m else None


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:120]


def merge_news(batches, now: float) -> list:
    """De-duplicate (same link, or same headline) across providers, newest first. The first
    provider in the list wins a duplicate and inherits the tickers of the others."""
    seen_url, seen_title, out = {}, {}, []
    for items in batches:
        for it in items:
            if it["t"] > now + 3600 or it["t"] < now - NEWS_MAX_AGE_DAYS * 86400:
                continue                                    # a date in the future / long past: not news
            u, k = _norm_url(it.get("url")), _norm_title(it["title"])
            first = (seen_url.get(u) if u else None) or (seen_title.get(k) if len(k) >= 24 else None)
            if first is not None:
                for field in ("symbols", "ids", "topics"):
                    for v in it.get(field) or []:
                        if v not in first[field]:
                            first[field].append(v)
                continue
            it = dict(it, symbols=list(it.get("symbols") or []), ids=list(it.get("ids") or []),
                      topics=list(it.get("topics") or []))
            if u:
                seen_url[u] = it
            if len(k) >= 24:
                seen_title[k] = it
            out.append(it)
    out.sort(key=lambda x: (-x["t"], x["id"]))
    return out


@router.get("/api/news")
def news(topic: str = Query("", max_length=16), id: str = Query("", max_length=16),
         limit: int = Query(200)):
    """Merged headlines. `topic` ∈ energy macro markets equities fx crypto filings (empty = all);
    `id` = a terminal instrument (items about it: by ticker, else by keyword in the headline)."""
    topic = topic.strip().lower()
    if topic in ("all", "*"):
        topic = ""
    if topic and topic not in news_feeds.TOPICS:
        raise BadRequest("topic must be one of " + ", ".join(news_feeds.TOPICS))
    inst = universe.require(id.strip().upper())["id"] if id.strip() else None
    limit = max(1, min(limit, 500))

    providers, futures = [], {}
    for name, label, kind, key, ttl, stale_ttl, loader in _news_providers():
        entry = {"name": name, "label": label, "kind": kind, "ok": False, "state": "loading", "n": 0,
                 "error": None, "as_of": None, "ttl": ttl}
        providers.append(entry)
        off = _provider_off(name)
        if off:
            entry.update(state="off", error=off)
            continue
        futures[name] = _POOL.submit(cached, key, ttl, loader, stale_ttl)
    deadline, batches, now = time.time() + NEWS_WAIT, [], time.time()
    for entry in providers:
        fut = futures.get(entry["name"])
        if fut is None:
            continue
        try:
            got = fut.result(timeout=max(0.05, deadline - time.time()))
        except concurrent.futures.TimeoutError:
            entry.update(state="loading", error="still waiting for the source (slow answer); it joins on the next refresh")
            continue
        except ApiError as e:
            entry.update(state="error", error=_why(e))
            continue
        except Exception as e:                               # a parser bug must not take the whole page down
            entry.update(state="error", error=f"unexpected {type(e).__name__}")
            continue
        entry.update(ok=True, state="stale" if got.get("stale") else "ok", n=len(got["items"]), as_of=got.get("as_of"),
                     error=_why(got.get("error")) if got.get("stale") else None)
        batches.append(got["items"])
    items = merge_news(batches, now)
    total = len(items)
    if topic:
        items = [it for it in items if topic in it["topics"]]
    if inst:
        items = [it for it in items if inst in it["ids"]]
    stamps = [p["as_of"] for p in providers if p["as_of"]]
    answered = [p for p in providers if p["state"] in ("ok", "stale")]
    if not answered and not any(p["state"] == "loading" for p in providers):
        raise NoSource("no news provider answered: " + "; ".join(
            f"{p['label']}: {p['error']}" for p in providers if p["error"])[:400])
    kinds = []
    for p in answered:
        name = {"edgar": "sec"}.get(p["kind"], p["kind"])
        if name not in kinds:
            kinds.append(name)
    out = {"source": " + ".join(kinds) or "news", "freshness": "D5", "as_of": max(stamps) if stamps else now,
           "oldest_as_of": min(stamps) if stamps else None, "ttl": NEWS_TTL, "topics": list(news_feeds.TOPICS),
           "topic": topic or None, "id": inst, "total": total, "matched": len(items),
           "providers": providers, "items": items[:limit]}
    if answered and all(p["state"] == "stale" for p in answered):       # nothing fresh at all: say so at the top
        out.update(stale=True, error={"code": "upstream_error", "message": "no news provider could be refreshed: "
                                      + "; ".join(f"{p['label']}: {p['error']}" for p in answered[:3])[:300]})
    return out


# =====================================================================================
# /api/calendar
# =====================================================================================
CALENDAR_TTL = 2 * 3600          # the export host rate-limits: ask rarely, keep the last good file on disk
CALENDAR_STORE = "ff_calendar_thisweek"
ACTUALS_MIN_TTL = 300            # released values are looked up at most this often: the page's polling interval


def _calendar_usable(value) -> bool:
    """A stored calendar is worth showing while it still describes the current week."""
    try:
        return bool(value["events"]) and max(ev["t"] for ev in value["events"]) > time.time() - 2 * 86400 \
            and all("title" in ev and "forecast" in ev for ev in value["events"])
    except (KeyError, TypeError, ValueError):
        return False


def _calendar_load() -> dict:
    disk = diskstore.load(CALENDAR_STORE)
    if disk and _calendar_usable(disk["value"]) and 0 <= time.time() - disk["at"] < CALENDAR_TTL:
        return disk["value"]                         # a gateway restart must not cost a request to a host that rate-limits
    events = forexfactory.parse(forexfactory.fetch())
    if not events:
        raise NoSource("the calendar export is empty")
    out = {"source": "forexfactory", "freshness": "SCHEDULE", "as_of": time.time(), "events": events}
    diskstore.save(CALENDAR_STORE, out)
    return out


def _calendar_base() -> dict:
    try:
        return cached("forexfactory:thisweek", CALENDAR_TTL, _calendar_load, stale_ttl=6 * 86400)
    except ApiError as e:
        disk = diskstore.load(CALENDAR_STORE)
        if disk and _calendar_usable(disk["value"]):
            return {**disk["value"], "stale": True, "error": describe(e)}
        raise


BLS_STORE = "bls_release_series"


def _bls_load(ids) -> dict:
    out = {"as_of": time.time(), "series": bls.parse_many(bls.fetch_many(ids))}
    if out["series"]:
        diskstore.save(BLS_STORE, out)               # BLS is often "busy": a released figure must survive that
    return out


def _bls_from_disk(ids):
    """The last good BLS answer, if it holds every series asked for. A released figure does not
    change, and actuals.compute() still refuses an observation of the wrong period."""
    disk = diskstore.load(BLS_STORE)
    value = disk["value"] if disk else None
    if isinstance(value, dict) and isinstance(value.get("series"), dict) and all(i in value["series"] for i in ids):
        return value
    return None


def _fred_obs(series_id: str, limit: int, ttl: float) -> dict:
    bucket = 12 if limit <= 12 else 120 if limit <= 120 else 1000
    return cached(f"fred:{series_id}:{bucket}", ttl,
                  lambda: {"as_of": time.time(), "obs": fred.parse_observations(fred.fetch_observations(series_id, bucket))},
                  stale_ttl=7 * 86400)


def _eia_obs(series_id: str, length: int, ttl: float) -> dict:
    bucket = 12 if length <= 12 else 120 if length <= 120 else 520
    def load():
        payload = eia.fetch_series(series_id, bucket)
        by = eia.parse_by_series(payload)
        if not by:
            raise NoSource(f"EIA has no observations for {series_id}")
        s = next(iter(by.values()))
        return {"as_of": time.time(), "unit": s["unit"], "desc": s["desc"], "obs": s["obs"]}
    return cached(f"eia:{series_id}:{bucket}", ttl, load, stale_ttl=14 * 86400)


def _fill_actuals(events: list, now: float) -> dict:
    """Mutates `events`: adds actual / actual_source / actual_period / actual_derived / actual_note.
    Returns {"sources": {name: {ok, error, as_of}}, "mapped": n, "filled": n}."""
    due = [(e, actuals.spec_for(e)) for e in events]
    for e, spec in due:
        e.update(actual=None, actual_source=None, actual_period=None, actual_derived=False, actual_note=None)
        e["numeric"] = bool(e["forecast"] or e["previous"] or spec)     # a speech or a holiday has no figure
        if e["impact"] == "holiday":
            continue
        if not e["numeric"]:
            e["actual_note"] = "no figure is published for this event"
        elif e["t"] > now:
            e["actual_note"] = "not released yet"
        elif spec is None:
            e["actual_note"] = ("released — no free official source is wired for this event's value; "
                                "see the publisher")
    due = [(e, s) for e, s in due if s is not None and e["t"] <= now]
    sources: dict = {}
    if not due:
        return {"sources": sources, "mapped": sum(1 for e in events if actuals.spec_for(e)), "filled": 0}
    ttl = ACTUALS_MIN_TTL if any(now - e["t"] < 3 * 3600 for e, _ in due) else 1800     # fresh release: look again soon
    data: dict = {}
    bls_ids = sorted({s["series"] for _, s in due if s["src"] == "bls"})
    if bls_ids:
        try:
            got = cached("bls:" + ",".join(bls_ids), ttl, lambda: _bls_load(bls_ids), stale_ttl=7 * 86400)
            sources["bls"] = {"ok": not got.get("stale"), "as_of": got["as_of"],
                              "error": _why(got.get("error")) if got.get("stale") else None}
            data.update({("bls", k): v for k, v in got["series"].items()})
        except ApiError as e:
            kept = _bls_from_disk(bls_ids)
            sources["bls"] = {"ok": False, "as_of": kept["as_of"] if kept else None, "error": _why(e)}
            if kept:
                data.update({("bls", k): v for k, v in kept["series"].items()})
    for src, getter in (("fred", _fred_obs), ("eia", _eia_obs)):
        for sid in sorted({s["series"] for _, s in due if s["src"] == src}):
            try:
                got = getter(sid, 12, ttl)
                row = sources.setdefault(src, {"ok": True, "as_of": got["as_of"], "error": None})
                if got.get("stale"):
                    row.update(ok=False, error=_why(got.get("error")))
                data[(src, sid)] = got["obs"]
            except ApiError as e:
                sources[src] = {"ok": False, "as_of": None, "error": _why(e)}
    filled = 0
    for e, spec in due:
        label = f"{actuals.SOURCE_LABEL[spec['src']]} {spec['series']}"
        series = data.get((spec["src"], spec["series"]))
        got = actuals.compute(spec, e["t"], series) if series else None
        if got:
            e.update(actual=got["actual"], actual_source=label, actual_period=got["actual_period"],
                     actual_derived=got["derived"],
                     actual_note=("computed from two observations of the official series"
                                  if got["derived"] else "the publisher's own figure"))
            filled += 1
        elif series is None:
            e["actual_note"] = f"released — {label} could not be read: {(sources.get(spec['src']) or {}).get('error')}"
        elif not (sources.get(spec["src"]) or {}).get("ok", True):
            e["actual_note"] = (f"released — {label} could not be refreshed ({sources[spec['src']]['error']}); the last "
                                f"answer kept here does not hold the {actuals.expected_period(spec, e['t'])} observation")
        else:
            e["actual_note"] = (f"released — {label} does not show the {actuals.expected_period(spec, e['t'])} "
                                "observation yet")
    return {"sources": sources, "mapped": sum(1 for e in events if actuals.spec_for(e)), "filled": filled}


@router.get("/api/calendar")
def calendar():
    """This week's events (UTC epoch seconds), with `actual` filled only from an official series."""
    base = _calendar_base()
    events = [dict(e) for e in base["events"]]
    now = time.time()
    info = _fill_actuals(events, now)
    out = _readable({k: v for k, v in base.items() if k != "events"})
    out.update(week={"from": events[0]["t"], "to": events[-1]["t"]}, actuals=info, ttl=ACTUALS_MIN_TTL, events=events,
               note="Schedule, forecast and previous: ForexFactory weekly export (times converted to UTC). "
                    "Actual: only where an official series (BLS, FRED, EIA) holds this release; empty otherwise.")
    return out


# =====================================================================================
# reference routes
# =====================================================================================
_EIA_FRESHNESS = {"W": "WEEKLY", "M": "MONTHLY", "A": "ANNUAL", "Q": "QUARTERLY", "D": "EOD", "H": "D60"}


@router.get("/api/eia")
def eia_series(series_id: str = Query("PET.WCESTUS1.W", max_length=64), length: int = Query(52)):
    """One EIA series, oldest first. series_id like PET.WCESTUS1.W (validated)."""
    series_id = series_id.strip().upper()
    if not eia.SERIES_ID.match(series_id):
        raise BadRequest("series_id must look like PET.WCESTUS1.W (letters, digits, '_', '-', '.')")
    length = max(1, min(length, 520))
    got = _eia_obs(series_id, length, 900)
    out = {"source": "eia", "freshness": _EIA_FRESHNESS.get(series_id[-1], "WEEKLY"), "series": series_id,
           "unit": got["unit"], "desc": got["desc"], "as_of": got["as_of"], "obs": got["obs"][-length:]}
    if got.get("stale"):
        out.update(stale=True, error=got.get("error"))
    return out


@router.get("/api/macro")
def macro(series_id: str = Query("DCOILWTICO", max_length=40), limit: int = Query(120)):
    """One FRED series, oldest first; `value` is null where FRED has none (holidays)."""
    series_id = series_id.strip().upper()
    if not fred.SERIES_ID.match(series_id):
        raise BadRequest("series_id must be a FRED id such as DGS10 (letters, digits, underscore)")
    limit = max(1, min(limit, 1000))
    got = _fred_obs(series_id, limit, 900)
    out = {"source": "fred", "series": series_id, "as_of": got["as_of"], "obs": got["obs"][-limit:]}
    if got.get("stale"):
        out.update(stale=True, error=got.get("error"))
    return out


@router.get("/api/trade")
def trade(reporter: str = Query("842", max_length=6), commodity: str = Query("2709", max_length=6),
          year: str = Query("", max_length=4), flow: str = Query("M", max_length=1)):
    """UN Comtrade annual flows of one reporter and HS code, by partner (842 = USA, 2709 = crude)."""
    year = year.strip() or str(time.gmtime(time.time()).tm_year - 1)
    flow = flow.strip().upper()
    if not (reporter.isdigit() and commodity.isdigit() and re.match(r"^(19|20)\d\d$", year) and flow in ("M", "X")):
        raise BadRequest("reporter and commodity are numeric codes, year is YYYY, flow is M or X")
    def load():
        parsed = comtrade.parse_flows(comtrade.fetch_flows(reporter, commodity, year, flow))
        return {"source": "un-comtrade", "freshness": "ANNUAL", "as_of": time.time(), "reporter": reporter,
                "commodity": commodity, "flow": flow, "year": parsed["year"] or int(year),
                "total": parsed["total"], "rows": parsed["rows"]}
    return cached(f"comtrade:{reporter}:{commodity}:{year}:{flow}", PARTNERS_TTL, load, stale_ttl=30 * 86400)


@router.get("/api/filings")
def filings(id: str = Query("", max_length=16), q: str = Query("", max_length=80),
            forms: str = Query("8-K,10-Q,10-K", max_length=60)):
    """SEC EDGAR full-text search over the last 30 days: by terminal equity (`id`) and/or text (`q`)."""
    tickers = []
    if id.strip():
        inst = universe.require(id.strip().upper())
        if inst["id"] not in edgar.CIKS:
            raise NoSource(f"{inst['id']} is not a company that files with the SEC — filings exist for: "
                           + " · ".join(edgar.CIKS))
        tickers = [inst["id"]]
    q = q.strip()
    form_list = [f.strip().upper() for f in forms.split(",") if f.strip()]
    if not q and not tickers:
        raise BadRequest("give id (AAPL, NVDA, TSLA, XOM) or a search text q")
    edgar.check_search(q, form_list)
    def load():
        rows = edgar.parse_search(edgar.fetch_search(q, form_list, ciks=[edgar.CIKS[t] for t in tickers]))
        return {"source": "sec-edgar", "freshness": "EOD", "as_of": time.time(), "id": tickers[0] if tickers else None,
                "q": q or None, "forms": form_list, "filings": rows[:100]}
    return cached(f"edgar:{','.join(tickers)}:{q.lower()}:{','.join(form_list)}", EDGAR_TTL, load, stale_ttl=86400)


@router.get("/api/factors")
def factors(dataset: str = Query("factors_monthly", max_length=24), limit: int = Query(360)):
    """Fama-French factor returns (percent), oldest first."""
    dataset = dataset.strip().lower()
    if dataset not in french.FILES:
        raise BadRequest("dataset must be one of " + ", ".join(french.FILES))
    limit = max(1, min(limit, 5000))
    got = cached(f"french:{dataset}", 24 * 3600,
                 lambda: {"source": "ken-french", "dataset": dataset, "as_of": time.time(),
                          "freshness": "EOD" if dataset.endswith("_daily") else "MONTHLY",
                          "rows": french.parse(french.fetch(dataset), dataset)}, stale_ttl=30 * 86400)
    return {**got, "rows": got["rows"][-limit:]}


@router.get("/api/worldbank")
def wb(country: str = Query("US", max_length=3), code: str = Query("NY.GDP.MKTP.CD", max_length=40)):
    """One World Bank annual indicator for one country, oldest first."""
    country, code = country.strip().upper(), code.strip().upper()
    worldbank.check(country, code)
    def load():
        parsed = worldbank.parse(worldbank.fetch(country, code))
        return {"source": "world-bank", "freshness": "ANNUAL", "as_of": time.time(), "country": country,
                "code": code, **parsed}
    return cached(f"worldbank:{country}:{code}", 24 * 3600, load, stale_ttl=30 * 86400)


@router.get("/api/usda")
def usda_stats(commodity: str = Query("CORN", max_length=40), statistic: str = Query("PRODUCTION", max_length=40),
               year: str = Query("2024", max_length=4)):
    """USDA NASS QuickStats national rows (numeric values; several units per commodity: pick by `unit`)."""
    commodity, statistic, year = commodity.strip().upper(), statistic.strip().upper(), year.strip()
    usda.check_quickstats(commodity, statistic, year)
    def load():
        return {"source": "usda-nass", "freshness": "ANNUAL", "as_of": time.time(), "commodity": commodity,
                "statistic": statistic, "year": year,
                "rows": usda.parse_quickstats(usda.fetch_quickstats(commodity, statistic, year))}
    return cached(f"usda-nass:{commodity}:{statistic}:{year}", 6 * 3600, load, stale_ttl=30 * 86400)
