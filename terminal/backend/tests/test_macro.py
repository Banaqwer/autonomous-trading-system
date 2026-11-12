"""MACRO slice tests (S6): provider parsers on fixtures recorded from the real sources on
2026-11-12 (tests/fixtures/s6/, trimmed, no key material), the three page routes
(/api/supply/oil, /api/news, /api/calendar) and the reference routes. Network-free: every
provider fetch function is replaced by one that returns a fixture or raises.

`ff_calendar_thisweek.json` holds the 106 real events of the week of 2026-11-09, rebuilt
in the export's own shape from the gateway's parsed copy (the host allows only a few
requests a day and the raw bytes were not kept).
"""
import calendar as _cal
import inspect
import json
import os
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as gateway
from core import cache, http
from core.errors import BadRequest, NotConfigured, RateLimited, UpstreamError
from providers import (actuals, bls, comtrade, diskstore, edgar, eia, forexfactory, fred, french,
                       marketaux, news, usda, worldbank)
from routes import macro

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "s6")
_REAL = {"eia.fetch_weekly": eia.fetch_weekly, "forexfactory.fetch": forexfactory.fetch,
         "fred.fetch_observations": fred.fetch_observations}
RECORDED_AT = 1794496500.0            # 2026-11-12 15:15:00 UTC, just after the fixtures were recorded


def raw(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def fx(name):
    return json.loads(raw(name))


def utc(y, mo, d, h=0, mi=0):
    return float(_cal.timegm((y, mo, d, h, mi, 0)))


@pytest.fixture()
def client():
    return TestClient(gateway.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    cache.clear()
    monkeypatch.setattr(diskstore, "DIR", str(tmp_path / "cache"))        # never the real backend/.cache
    monkeypatch.setattr(forexfactory, "_blocked", {"until": 0.0})
    for name in ("EIA_API_KEY", "FRED_API_KEY", "BLS_API_KEY", "MARKETAUX_API_KEY", "APCA_API_KEY_ID",
                 "APCA_API_SECRET_KEY", "NASS_API_KEY"):
        monkeypatch.setenv(name, "test-" + name.lower())
    monkeypatch.delenv("OT_MARKETAUX", raising=False)
    monkeypatch.delenv("COMTRADE_API_KEY", raising=False)
    yield
    cache.clear()


class Upstreams:
    """Every network function of the slice -> fixture. `calls` counts them; `down` fails them."""

    def __init__(self, mp):
        self.calls, self.down = {}, set()
        w = self._wrap
        mp.setattr(eia, "fetch_weekly", w("eia", lambda series, start, **k: fx("eia_weekly_supply.json")))
        mp.setattr(eia, "fetch_series", w("eia_series", self._eia_series))
        mp.setattr(comtrade, "fetch_flows", w("comtrade", self._comtrade))
        mp.setattr(news, "fetch_alpaca", w("alpaca", lambda symbols=None, **k: fx("alpaca_news.json")))
        mp.setattr(news, "fetch_rss", w("rss", lambda key, **k: self._rss(key).encode("utf-8")))
        mp.setattr(marketaux, "fetch_headlines", w("marketaux", lambda **k: {
            "payload": fx("marketaux_news.json"), "usage": {"limit": 100.0, "remaining": 94.0}}))
        mp.setattr(edgar, "fetch_search", w("edgar", lambda *a, **k: fx("edgar_search_8k.json")))
        mp.setattr(forexfactory, "fetch", w("forexfactory", lambda **k: fx("ff_calendar_thisweek.json")))
        mp.setattr(bls, "fetch_many", w("bls", lambda ids, *a, **k: fx("bls_calendar.json")))
        mp.setattr(fred, "fetch_observations", w("fred", lambda sid, limit=120, **k: fx(f"fred_{sid}.json")))
        mp.setattr(french, "fetch", w("french", lambda dataset="factors_monthly", **k: raw("french_factors_monthly.csv")))
        mp.setattr(worldbank, "fetch", w("worldbank", lambda *a, **k: fx("worldbank_gdp_us.json")))
        mp.setattr(usda, "fetch_quickstats", w("usda", lambda *a, **k: fx("nass_corn_production.json")))

    def _wrap(self, name, fn):
        def call(*a, **k):
            self.calls[name] = self.calls.get(name, 0) + 1
            if name in self.down:
                raise UpstreamError(name, 503, "service unavailable (test)")
            return fn(*a, **k)
        return call

    def _eia_series(self, series_id, length=52, **k):
        full = fx("eia_weekly_supply.json")
        sid = series_id.split(".")[1]
        rows = [r for r in full["response"]["data"] if r["series"] == sid]
        return {"response": {"data": rows[:length]}}

    def _comtrade(self, reporter="842", commodity="2709", year="2024", flow="M", **k):
        name = f"comtrade_crude_imports_{year}.json"
        if not os.path.exists(os.path.join(FIX, name)):
            return {"data": [], "count": 0, "error": ""}
        return fx(name)

    def _rss(self, key, **k):
        name = {"oilprice": "rss_oilprice.xml", "fed": "rss_fed.xml", "bbc-business": "rss_bbc.xml",
                "marketwatch": "rss_marketwatch.xml"}.get(key)
        if name is None:
            raise UpstreamError("rss-" + key, 404, "no fixture for this feed (test)")
        return raw(name)


@pytest.fixture()
def up(monkeypatch):
    return Upstreams(monkeypatch)


@pytest.fixture()
def clock(monkeypatch):
    class Clock:
        now = RECORDED_AT

        def tick(self, s):
            self.now += s
    c = Clock()
    monkeypatch.setattr(time, "time", lambda: c.now)
    return c


# ===================================================================== EIA / supply chain
def test_eia_weekly_parser_groups_series_ascending_with_units():
    by = eia.parse_by_series(fx("eia_weekly_supply.json"))
    assert len(by) == 19
    stocks = by["WCESTUS1"]
    assert stocks["unit"] == "kbbl" and "Ending Stocks excluding SPR" in stocks["desc"]
    assert [o["date"] for o in stocks["obs"][-3:]] == ["2026-10-16", "2026-10-23", "2026-10-30"]
    assert [o["value"] for o in stocks["obs"][-3:]] == [412517.0, 410625.0, 409760.0]
    assert stocks["obs"][-1]["t"] == utc(2026, 10, 30)
    assert by["WPULEUS3"]["unit"] == "%" and by["WCRFPUS2"]["unit"] == "kb/d"
    for s in by.values():
        ts = [o["t"] for o in s["obs"]]
        assert ts == sorted(set(ts)) and all(isinstance(o["value"], float) for o in s["obs"])


def test_eia_parser_rejects_an_unexpected_shape_and_drops_rows_without_a_number():
    with pytest.raises(UpstreamError):
        eia.parse_by_series({"error": "nope"})
    by = eia.parse_by_series({"response": {"data": [
        {"series": "X", "period": "2026-10-30", "value": None, "units": "MBBL"},
        {"series": "X", "period": "2026-10-23", "value": "12", "units": "MBBL"},
        {"series": "X", "period": "garbage", "value": "1", "units": "MBBL"}]}})
    assert by["X"]["obs"] == [{"date": "2026-10-23", "t": utc(2026, 10, 23), "value": 12.0}]


def test_eia_key_travels_in_a_header_never_in_the_url(monkeypatch):
    seen = {}

    def fake(url, headers=None, timeout=10, source=None):
        seen.update(url=url, headers=headers, source=source)
        return {"response": {"data": []}}
    monkeypatch.setattr(http, "get_json", fake)
    monkeypatch.setenv("EIA_API_KEY", "SECRET-EIA-KEY")
    eia.fetch_weekly(["WCESTUS1", "WCRFPUS2"], "2024-10-01")
    assert "SECRET-EIA-KEY" not in seen["url"] and "api_key" not in seen["url"].lower()
    assert seen["headers"] == {"X-Api-Key": "SECRET-EIA-KEY"} and seen["source"] == "eia"
    assert "facets[series][]=WCESTUS1&facets[series][]=WCRFPUS2" in seen["url"] and "start=2024-10-01" in seen["url"]
    eia.fetch_series("PET.WCESTUS1.W", 5)
    assert "SECRET-EIA-KEY" not in seen["url"] and seen["url"].endswith("/seriesid/PET.WCESTUS1.W?length=5")
    for bad in ("PET.WCESTUS1.W/../x", "pet.wcestus1.w?api_key=1", "A B", ""):
        with pytest.raises(BadRequest):
            eia.fetch_series(bad)
    with pytest.raises(BadRequest):
        eia.fetch_weekly(["WCESTUS1&x=1"], "2024-10-01")
    monkeypatch.delenv("EIA_API_KEY")
    with pytest.raises(NotConfigured):
        eia.fetch_weekly(["WCESTUS1"], "2024-10-01")


def test_eia_series_helper_is_oldest_first_for_the_research_app(up):
    rows = eia.series("PET.WCESTUS1.W", 4)
    assert [r["date"] for r in rows] == ["2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30"]
    assert rows[-1] == {"date": "2026-10-30", "value": 409760.0}        # [-1] is the latest week


def test_supply_oil_nodes_carry_real_value_unit_week_change_and_history(client, up):
    r = client.get("/api/supply/oil")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "eia" and d["freshness"] == "WEEKLY" and d["week"] == "2026-10-30"
    assert d["week_t"] == utc(2026, 10, 30) and isinstance(d["as_of"], float) and "stale" not in d
    nodes = {n["key"]: n for n in d["nodes"]}
    assert set(nodes) == {"production", "imports", "net_imports", "stocks", "padd1", "padd2", "padd3", "padd4",
                          "padd5", "cushing", "spr", "refinery_inputs", "utilization", "exports", "gasoline",
                          "distillate", "jet", "product_supplied"}
    s = nodes["stocks"]
    assert (s["value"], s["prev"], s["chg"], s["unit"], s["week"], s["prev_week"]) == \
           (409760.0, 410625.0, -865.0, "kbbl", "2026-10-30", "2026-10-23")
    assert s["chg_pct"] == pytest.approx(-0.211, abs=1e-3) and s["series"] == "PET.WCESTUS1.W" and s["tight"] == "down"
    assert nodes["cushing"]["value"] == 27250.0 and nodes["cushing"]["prev"] == 26970.0
    assert nodes["exports"]["value"] == 3675.0 and nodes["exports"]["chg"] == -1179.0 and nodes["exports"]["tight"] == "up"
    assert nodes["utilization"]["value"] == 94.5 and nodes["utilization"]["unit"] == "%"
    assert nodes["production"]["unit"] == "kb/d" and nodes["spr"]["tight"] is None
    # the five PADDs add up to the national commercial stock figure (EIA's own identity)
    assert sum(nodes[f"padd{i}"]["value"] for i in range(1, 6)) == s["value"]
    for n in d["nodes"]:
        h = n["history"]
        assert len(h) == 30 and h == sorted(h) and h[-1] == [n["week_t"], n["value"]]
        assert n["label"] and n["desc"] and n["stage"] in ("supply", "stocks", "refining", "products")


def test_supply_oil_partners_are_annual_and_labelled_with_their_year(client, up, clock):
    d = client.get("/api/supply/oil").json()
    p = d["partners"]
    assert p["source"] == "un-comtrade" and p["freshness"] == "ANNUAL" and p["year"] == 2025
    assert p["total"]["value_usd"] == 146846995268.0
    top = p["rows"][0]
    assert (top["name"], top["iso"], top["code"], top["value_usd"], top["share"]) == \
           ("Canada", "CAN", 124, 90155790541.0, 61.39)
    assert [r["name"] for r in p["rows"][:3]] == ["Canada", "Mexico", "Saudi Arabia"]
    assert all(r["code"] != 0 for r in p["rows"]) and len({r["code"] for r in p["rows"]}) == len(p["rows"])
    values = [r["value_usd"] for r in p["rows"]]
    assert values == sorted(values, reverse=True)
    assert up.calls["comtrade"] == 1                       # 2025 answered: 2024 was not asked
    cache.clear()                                          # a gateway restart the same day: the annual table comes from disk
    again = client.get("/api/supply/oil").json()["partners"]
    assert up.calls["comtrade"] == 1 and again["rows"] == p["rows"] and again["as_of"] == p["as_of"]
    clock.tick(macro.PARTNERS_TTL + 1)
    cache.clear()
    client.get("/api/supply/oil")
    assert up.calls["comtrade"] == 2


def test_partners_fall_back_one_year_when_the_newest_year_is_empty(client, up, clock, monkeypatch):
    clock.now = utc(2027, 2, 1)                             # "last year" = 2026: Comtrade has nothing yet
    d = client.get("/api/supply/oil").json()
    assert d["partners"]["year"] == 2025 and up.calls["comtrade"] == 2


def test_supply_oil_is_one_upstream_call_however_often_it_is_asked(client, up):
    for _ in range(5):
        assert client.get("/api/supply/oil").status_code == 200
    assert up.calls["eia"] == 1 and up.calls["comtrade"] == 1


def test_supply_oil_serves_the_last_week_marked_stale_when_eia_fails(client, up, clock):
    assert client.get("/api/supply/oil").status_code == 200
    up.down.add("eia")
    clock.tick(macro.OIL_TTL + 5)
    d = client.get("/api/supply/oil").json()
    assert d["stale"] is True and d["error"]["code"] == "upstream_error" and d["week"] == "2026-10-30"
    assert d["nodes"][0]["value"] is not None


def test_supply_oil_without_any_answer_is_a_typed_error_and_comtrade_alone_does_not_break_it(client, up, monkeypatch):
    up.down.add("comtrade")
    d = client.get("/api/supply/oil").json()
    assert d["nodes"] and d["partners"]["error"]["code"] == "upstream_error" and "rows" not in d["partners"]
    cache.clear()
    up.down.add("eia")
    r = client.get("/api/supply/oil")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"
    cache.clear()
    up.down.clear()
    monkeypatch.setattr(eia, "fetch_weekly", _REAL["eia.fetch_weekly"])
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    monkeypatch.setattr(http, "get_json", lambda *a, **k: pytest.fail("must not call out without a key"))
    r = client.get("/api/supply/oil")
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_comtrade_never_sends_partner_code_and_validates_its_parameters(monkeypatch):
    seen = {}
    monkeypatch.setattr(http, "get_json", lambda url, headers=None, timeout=10, source=None:
                        seen.update(url=url, headers=headers) or {"data": []})
    comtrade.fetch_flows("842", "2709", "2025", "M")
    assert "partnerCode" not in seen["url"] and "includeDesc=true" in seen["url"] and "/public/v1/preview/" in seen["url"]
    monkeypatch.setenv("COMTRADE_API_KEY", "SECRET-CT")
    comtrade.fetch_flows("842", "2709", "2025", "M")
    assert "SECRET-CT" not in seen["url"] and seen["headers"] == {"Ocp-Apim-Subscription-Key": "SECRET-CT"}
    for args in (("84 2", "2709", "2025", "M"), ("842", "27&x", "2025", "M"), ("842", "2709", "20x5", "M"),
                 ("842", "2709", "2025", "Q")):
        with pytest.raises(BadRequest):
            comtrade.fetch_flows(*args)
    with pytest.raises(UpstreamError):
        comtrade.parse_flows({"error": "Invalid parameter value"})


# ===================================================================== news
def test_alpaca_news_parser(up):
    items = news.parse_alpaca(fx("alpaca_news.json"))
    assert len(items) == 20
    first = items[0]
    assert first["provider"] == "alpaca" and first["source"] == "benzinga" and first["url"].startswith("https://")
    assert first["t"] == utc(2026, 11, 12, 14, 56) and first["symbols"] == ["FIGR"] and "equities" in first["topics"]
    assert all("\n" not in i["title"] and i["title"] == i["title"].strip() for i in items)
    nvda = [i for i in items if "NVDA" in i["symbols"]]
    assert nvda and all("NVDA" in i["ids"] for i in nvda)
    with pytest.raises(UpstreamError):
        news.parse_alpaca({"message": "forbidden"})


def test_rss_parser_reads_dates_links_and_plain_text():
    oil = news.parse_rss(raw("rss_oilprice.xml"), "oilprice")
    assert len(oil) == 5 and all(i["source"] == "OilPrice.com" and "energy" in i["topics"] for i in oil)
    assert [i["t"] for i in oil] == sorted((i["t"] for i in oil), reverse=True)
    assert all(i["url"].startswith("https://oilprice.com/") for i in oil)
    assert all("<" not in i["title"] and "<" not in i["summary"] and "&amp;" not in i["title"] for i in oil)
    fed = news.parse_rss(raw("rss_fed.xml"), "fed")                       # dates and titles wrapped in CDATA
    assert len(fed) == 4 and all(i["t"] > utc(2026, 10, 1) and "macro" in i["topics"] for i in fed)
    for name, key in (("rss_bbc.xml", "bbc-business"), ("rss_marketwatch.xml", "marketwatch")):
        got = news.parse_rss(raw(name), key)
        assert got and all(i["title"] and i["t"] and i["provider"] == "rss" for i in got)


def test_rss_is_untrusted_markup_is_stripped_bad_links_dropped_dtds_refused():
    hostile = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
      <item><title><![CDATA[<img src=x onerror=alert(1)>Oil <b>jumps</b> &amp; <script>alert(2)</script>falls]]></title>
        <link>javascript:alert(3)</link><pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate>
        <description>&lt;a href="https://evil.example"&gt;click&lt;/a&gt; now</description></item>
      <item><title>No date at all</title><link>https://example.com/a</link></item>
      <item><title>   </title><link>https://example.com/b</link><pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item>
      <item><title>Odd link</title><link>https://exa mple.com/\nx</link><pubDate>2026-11-12T09:00:00Z</pubDate></item>
    </channel></rss>"""
    items = news.parse_rss(hostile, "oilprice")
    assert [i["title"] for i in items] == ["Oil jumps & alert(2) falls", "Odd link"]
    assert items[0]["url"] is None and items[1]["url"] is None            # javascript: / malformed -> no link
    assert items[0]["summary"] == "click now" and items[0]["t"] == utc(2026, 11, 12, 10)
    for bomb in ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title>'
                 '</item></channel></rss>', "<rss><channel><item><title>unclosed", "", "   "):
        with pytest.raises(UpstreamError):
            news.parse_rss(bomb, "oilprice")
    # a feed in another encoding (the EIA feeds are ISO-8859-1) is parsed from its bytes, not mis-decoded
    latin = ('<?xml version="1.0" encoding="ISO-8859-1" ?><rss><channel><item><title>Pétrole: résumé du marché</title>'
             '<link>https://example.com/p</link><pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>').encode("latin-1")
    assert news.parse_rss(latin, "eia-today")[0]["title"] == "Pétrole: résumé du marché"
    assert news.parse_rss(b"\xef\xbb\xbf" + raw("rss_fed.xml").encode("utf-8"), "fed").__len__() == 4       # UTF-8 BOM
    assert news.safe_url("https://a.example/x?y=1") == "https://a.example/x?y=1"
    for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.example", "ftp://x.example/a", "https://", None, 5):
        assert news.safe_url(bad) is None


def test_marketaux_parser_and_its_helpers_never_raise_for_the_research_app(monkeypatch):
    items = marketaux.parse_headlines(fx("marketaux_news.json"))
    assert len(items) == 3 and items[0]["provider"] == "marketaux" and items[0]["source"] == "seekingalpha.com"
    assert items[0]["t"] == utc(2026, 11, 12, 15, 19) + 3 and items[0]["url"].startswith("https://seekingalpha.com/")
    assert items[0]["symbols"] == ["ACXP"] and "equities" in items[0]["topics"]
    assert "QQQ" in items[1]["symbols"] and "NDX" in items[1]["ids"]            # ticker -> terminal instrument
    assert all(re.match(r"^[A-Z0-9./-]{1,12}$", s) for i in items for s in i["symbols"])   # "^CNXIT" is not passed on
    with pytest.raises(UpstreamError):
        marketaux.parse_headlines({"error": {"code": "usage_limit_reached"}})
    monkeypatch.setattr(http, "request", lambda *a, **k: (_ for _ in ()).throw(UpstreamError("marketaux", 500, "x")))
    assert marketaux.theme_sentiment("oil") is None and marketaux.symbol_sentiment(["AAPL"]) is None
    monkeypatch.delenv("MARKETAUX_API_KEY")
    assert marketaux.enabled() is False and marketaux.theme_sentiment("oil") is None


def test_news_route_merges_dedupes_sorts_and_reports_every_provider(client, up, clock):
    r = client.get("/api/news?limit=500")
    assert r.status_code == 200, r.text
    d = r.json()
    by = {p["name"]: p for p in d["providers"]}
    assert set(by) == {"alpaca", "alpaca-watch", "marketaux", "edgar"} | {"rss-" + k for k in news.FEEDS}
    assert by["alpaca"]["state"] == "ok" and by["alpaca"]["ok"] is True and by["alpaca"]["n"] == 20
    assert by["rss-oilprice"]["ok"] is True and by["rss-oilprice"]["n"] == 5 and by["rss-oilprice"]["label"] == "OilPrice.com"
    # a feed that is down says so, by name, and does not take the others with it
    assert by["rss-ecb"]["ok"] is False and by["rss-ecb"]["state"] == "error" and "no fixture for this feed" in by["rss-ecb"]["error"]
    assert by["marketaux"]["n"] == 3 and by["edgar"]["n"] == 4
    items = d["items"]
    ts = [i["t"] for i in items]
    assert ts == sorted(ts, reverse=True) and len({i["id"] for i in items}) == len(items)
    # alpaca and alpaca-watch return the same articles in this fixture (20 rows, 19 distinct): each appears once
    assert sum(1 for i in items if i["provider"] == "alpaca") == 19
    # 44 distinct articles in the fixtures; two are older than 14 days (one of them the FOMC statement of
    # 2026-10-28 from the Federal Reserve feed) and are not news any more
    assert d["total"] == len(items) == 19 + 5 + 4 + 5 + 4 + 3 + 4 - 2
    assert sum(1 for i in items if i["source"] == "Federal Reserve") == 3
    assert min(i["t"] for i in items) >= RECORDED_AT - macro.NEWS_MAX_AGE_DAYS * 86400
    for i in items:
        assert set(i) >= {"id", "t", "provider", "source", "title", "url", "symbols", "ids", "topics", "summary"}
        assert i["url"] is None or i["url"].startswith(("http://", "https://"))
    filing = next(i for i in items if i["provider"] == "edgar")
    assert filing["date_only"] is True and filing["source"] == "SEC EDGAR" and filing["symbols"] == ["NVDA"]
    assert filing["title"].startswith("NVIDIA CORP filed 8-K: 5.02 Departure or appointment of directors or officers")
    assert filing["url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000082/0001045810-26-000082-index.htm"
    assert d["freshness"] == "D5" and d["ttl"] == 300 and d["as_of"] == RECORDED_AT
    assert d["source"] == "alpaca + rss + marketaux + sec"            # the providers that answered, in order


def test_news_filters_topic_and_instrument_and_validates_parameters(client, up, clock):
    energy = client.get("/api/news?topic=energy").json()
    assert energy["topic"] == "energy" and energy["items"] and all("energy" in i["topics"] for i in energy["items"])
    assert energy["matched"] == len(energy["items"]) < energy["total"]
    nvda = client.get("/api/news?id=nvda").json()
    assert nvda["id"] == "NVDA" and nvda["items"] and all("NVDA" in i["ids"] for i in nvda["items"])
    assert client.get("/api/news?limit=3").json()["items"].__len__() == 3
    assert client.get("/api/news?limit=-5").json()["items"].__len__() == 1
    assert client.get("/api/news?topic=all").status_code == 200
    for bad in ("/api/news?topic=sports", "/api/news?id=DOGE", "/api/news?limit=abc", "/api/news?topic=" + "x" * 40):
        r = client.get(bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", bad


def test_news_is_cached_per_provider_for_at_least_five_minutes(client, up, clock):
    for _ in range(4):
        client.get("/api/news")
    assert up.calls["alpaca"] == 2 and up.calls["marketaux"] == 1 and up.calls["edgar"] == 1
    assert up.calls["rss"] == len(news.FEEDS)
    clock.tick(299)
    client.get("/api/news")
    assert up.calls["alpaca"] == 2
    clock.tick(2)
    client.get("/api/news")
    assert up.calls["alpaca"] == 4 and up.calls["marketaux"] == 1          # Marketaux: 30 minutes
    assert macro.NEWS_TTL >= 300 and macro.RSS_TTL >= 300 and macro.MARKETAUX_TTL >= 900


def test_news_keeps_a_failed_provider_s_last_headlines_marked_stale(client, up, clock):
    client.get("/api/news")
    up.down.add("alpaca")
    clock.tick(macro.NEWS_TTL + 10)
    d = client.get("/api/news").json()
    p = {x["name"]: x for x in d["providers"]}["alpaca"]
    assert p["state"] == "stale" and p["ok"] is True and "service unavailable" in p["error"] and p["n"] == 20
    assert any(i["provider"] == "alpaca" for i in d["items"])
    assert "stale" not in d                                    # other feeds are fresh: the page as a whole is not stale
    for name in ("rss", "marketaux", "edgar"):
        up.down.add(name)
    clock.tick(macro.MARKETAUX_TTL + 10)
    d = client.get("/api/news").json()
    assert d["stale"] is True and "no news provider could be refreshed" in d["error"]["message"] and d["items"]


def test_news_with_every_provider_down_is_a_typed_error_not_an_empty_list(client, up, monkeypatch):
    for name in ("alpaca", "rss", "marketaux", "edgar"):
        up.down.add(name)
    r = client.get("/api/news")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_source"
    assert "no news provider answered" in r.json()["error"]["message"]


def test_a_slow_provider_is_reported_as_loading_and_does_not_hold_the_answer(client, up, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(marketaux, "fetch_headlines", lambda **k: (gate.wait(5), {
        "payload": fx("marketaux_news.json"), "usage": {"limit": 100.0, "remaining": 90.0}})[1])
    monkeypatch.setattr(macro, "NEWS_WAIT", 0.4)
    t0 = time.time()
    d = client.get("/api/news").json()
    assert time.time() - t0 < 3
    p = {x["name"]: x for x in d["providers"]}["marketaux"]
    assert p["state"] == "loading" and p["ok"] is False and not any(i["provider"] == "marketaux" for i in d["items"])
    gate.set()
    for _ in range(50):
        d = client.get("/api/news").json()
        if {x["name"]: x for x in d["providers"]}["marketaux"]["state"] == "ok":
            break
        time.sleep(0.05)
    assert any(i["provider"] == "marketaux" for i in d["items"])


def test_marketaux_budget_disk_copy_survives_a_restart_and_the_reserve_is_never_spent(client, up, clock, monkeypatch):
    client.get("/api/news")
    assert up.calls["marketaux"] == 1
    saved = diskstore.load("marketaux_headlines")
    assert saved and len(saved["value"]["items"]) == 3 and "test-marketaux" not in json.dumps(saved)
    assert diskstore.load("marketaux_budget")["value"] == {"day": "2026-11-12", "calls": 1, "remaining": 94.0}
    cache.clear()                                               # = a gateway restart
    clock.tick(600)
    d = client.get("/api/news").json()
    assert up.calls["marketaux"] == 1, "a restart inside the TTL must not cost a billed call"
    assert {x["name"]: x for x in d["providers"]}["marketaux"]["n"] == 3
    # the plan says 9 calls are left: below the reserve -> refused without calling out
    cache.clear()
    clock.tick(macro.MARKETAUX_TTL)
    diskstore.save("marketaux_budget", {"day": "2026-11-12", "calls": 5, "remaining": 9.0})
    d = client.get("/api/news").json()
    p = {x["name"]: x for x in d["providers"]}["marketaux"]
    assert up.calls["marketaux"] == 1 and p["ok"] is False and "daily budget protected" in p["error"]
    # the next UTC day the counter starts again
    cache.clear()
    clock.now = utc(2026, 11, 13, 0, 5)
    client.get("/api/news")
    assert up.calls["marketaux"] == 2 and diskstore.load("marketaux_budget")["value"]["day"] == "2026-11-13"


def test_marketaux_can_be_switched_off_and_is_off_without_a_key(client, up, monkeypatch):
    monkeypatch.setenv("OT_MARKETAUX", "0")
    d = client.get("/api/news").json()
    p = {x["name"]: x for x in d["providers"]}["marketaux"]
    assert p["state"] == "off" and "OT_MARKETAUX=0" in p["error"] and "marketaux" not in up.calls
    assert d["source"] == "alpaca + rss + sec"
    before = dict(up.calls)
    monkeypatch.delenv("OT_MARKETAUX")
    monkeypatch.delenv("MARKETAUX_API_KEY")
    monkeypatch.delenv("APCA_API_KEY_ID")
    cache.clear()
    d = client.get("/api/news").json()
    by = {x["name"]: x for x in d["providers"]}
    assert by["marketaux"]["state"] == "off" and "MARKETAUX_API_KEY" in by["marketaux"]["error"]
    assert by["alpaca"]["state"] == "off" and "APCA_API_KEY_ID" in by["alpaca"]["error"]
    assert up.calls["alpaca"] == before["alpaca"] and "marketaux" not in up.calls


def test_merge_news_first_provider_wins_a_duplicate_and_inherits_the_tickers():
    now = RECORDED_AT
    a = [{"id": "a1", "t": now - 60, "provider": "alpaca", "source": "benzinga", "title": "OPEC+ agrees to raise output in December",
          "url": "https://www.example.com/story/1?utm_source=x", "symbols": ["XOM"], "ids": ["XOM"], "topics": ["equities"], "summary": ""}]
    b = [{"id": "b1", "t": now - 30, "provider": "rss", "source": "Feed", "title": "Opec+ agrees to raise output in December!",
          "url": "https://example.com/other", "symbols": [], "ids": ["CL"], "topics": ["energy"], "summary": ""},
         {"id": "b2", "t": now - 10, "provider": "rss", "source": "Feed", "title": "Same link, new words",
          "url": "http://example.com/story/1/", "symbols": [], "ids": [], "topics": ["markets"], "summary": ""},
         {"id": "b3", "t": now + 7200, "provider": "rss", "source": "Feed", "title": "From the future", "url": None,
          "symbols": [], "ids": [], "topics": [], "summary": ""},
         {"id": "b4", "t": now - 40 * 86400, "provider": "rss", "source": "Feed", "title": "Ancient", "url": None,
          "symbols": [], "ids": [], "topics": [], "summary": ""}]
    out = macro.merge_news([a, b], now)
    assert [i["id"] for i in out] == ["a1"]
    assert out[0]["ids"] == ["XOM", "CL"] and out[0]["topics"] == ["equities", "energy", "markets"]
    assert a[0]["ids"] == ["XOM"], "cached provider items must not be mutated by a merge"


# ===================================================================== EDGAR
def test_edgar_search_parser_and_generic_default_user_agent(monkeypatch):
    rows = edgar.parse_search(fx("edgar_search_8k.json"))
    assert [r["ticker"] for r in rows] == ["NVDA", "AAPL", "AAPL", "TSLA"]
    r = rows[0]
    assert (r["company"], r["form"], r["filed"], r["cik"], r["accession"]) == \
           ("NVIDIA CORP", "8-K", "2026-11-12", "0001045810", "0001045810-26-000082")
    assert r["items"][0] == {"code": "5.02", "text": "Departure or appointment of directors or officers"}
    assert r["t"] == utc(2026, 11, 12)
    with pytest.raises(UpstreamError):
        edgar.parse_search({"message": "Forbidden"})
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert "@" not in edgar._ua()["User-Agent"] and "@" not in edgar.DEFAULT_UA
    monkeypatch.setenv("SEC_USER_AGENT", "Somebody somebody@example.org")
    assert edgar._ua()["User-Agent"] == "Somebody somebody@example.org"
    for kw in ({"q": "a" * 81}, {"q": "x&ciks=1"}, {"q": "<script>"}, {"forms": ["8-K&x=1"]}, {"start": "yesterday"}, {"ciks": ["12ab"]},
               {"q": "", "ciks": []}):
        args = {"q": "apple", "forms": ["8-K"]}
        args.update(kw)
        with pytest.raises(BadRequest):
            edgar.fetch_search(**args)


def test_filings_route(client, up):
    d = client.get("/api/filings?id=nvda&forms=8-K").json()
    assert d["source"] == "sec-edgar" and d["id"] == "NVDA" and d["filings"][0]["accession"] == "0001045810-26-000082"
    r = client.get("/api/filings?id=CL")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_source" and "AAPL" in r.json()["error"]["message"]
    assert client.get("/api/filings").status_code == 400
    assert client.get("/api/filings?id=DOGE").status_code == 400
    assert client.get("/api/filings?q=%3Cscript%3E").status_code == 400
    assert client.get("/api/filings?q=apple").status_code == 200
    client.get("/api/filings?q=apple")
    assert up.calls["edgar"] == 2                           # nvda + apple; the repeat came from the cache


# ===================================================================== calendar
def test_forexfactory_parser_converts_to_utc_and_keeps_the_source_s_figures():
    ev = forexfactory.parse(fx("ff_calendar_thisweek.json"))
    assert len(ev) == 106 and [e["t"] for e in ev] == sorted(e["t"] for e in ev)
    assert len({e["id"] for e in ev}) == 106
    cpi = next(e for e in ev if e["title"] == "CPI m/m")
    assert cpi == {"id": cpi["id"], "t": utc(2026, 11, 10, 13, 30), "currency": "USD", "country": "US",
                   "title": "CPI m/m", "impact": "high", "forecast": "0.3%", "previous": "0.6%"}
    claims = next(e for e in ev if e["title"] == "Unemployment Claims")
    assert (claims["t"], claims["impact"], claims["forecast"], claims["previous"]) == \
           (utc(2026, 11, 12, 13, 30), "medium", "194K", "189K")
    assert {e["impact"] for e in ev} == {"high", "medium", "low", "holiday"}
    assert {e["country"] for e in ev} >= {"US", "EU", "GB", "JP", "CN", "AU", "CA"}
    speech = next(e for e in ev if "Speaks" in e["title"])
    assert speech["forecast"] is None and speech["previous"] is None
    with pytest.raises(UpstreamError):
        forexfactory.parse({"error": "x"})
    assert forexfactory.parse([{"title": "", "date": "2026-11-10T08:30:00-05:00"}, {"title": "No date"}, 7]) == []


def test_calendar_fills_actuals_only_from_official_series_and_only_after_release(client, up, clock):
    r = client.get("/api/calendar")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "forexfactory" and d["freshness"] == "SCHEDULE" and d["as_of"] == RECORDED_AT
    assert d["week"] == {"from": d["events"][0]["t"], "to": d["events"][-1]["t"]}
    ev = {e["title"]: e for e in d["events"] if e["currency"] == "USD"}
    cpi = ev["CPI m/m"]
    assert (cpi["actual"], cpi["actual_source"], cpi["actual_period"], cpi["actual_derived"]) == \
           ("0.4%", "BLS CUSR0000SA0", "2026-10", False)
    assert ev["Core CPI m/m"]["actual"] == "0.3%"
    claims = ev["Unemployment Claims"]
    assert (claims["actual"], claims["actual_source"], claims["actual_period"]) == ("200K", "FRED ICSA", "2026-11-07")
    # not released yet (PPI is tomorrow, crude inventories later today): nothing, and it says why
    for title in ("PPI m/m", "Core PPI m/m", "Crude Oil Inventories", "Natural Gas Storage"):
        assert ev[title]["actual"] is None and ev[title]["actual_source"] is None
        assert ev[title]["actual_note"] == "not released yet"
    # released, but no official series is wired: empty, never guessed
    gdp = next(e for e in d["events"] if e["title"] == "GDP m/m" and e["currency"] == "GBP")
    assert gdp["actual"] is None and "no free official source" in gdp["actual_note"]
    assert d["actuals"]["mapped"] == 7 and d["actuals"]["filled"] == 3
    assert d["actuals"]["sources"]["bls"]["ok"] is True and d["actuals"]["sources"]["fred"]["ok"] is True
    assert all(e["actual"] is None for e in d["events"] if e["t"] > RECORDED_AT)
    assert up.calls["forexfactory"] == 1 and up.calls["bls"] == 1 and up.calls["fred"] == 1 and "eia_series" not in up.calls


def test_calendar_actual_appears_when_the_series_shows_the_week_and_is_marked_derived(client, up, clock):
    clock.now = utc(2026, 11, 12, 17, 30)                    # crude inventories (17:00) is out now
    d = client.get("/api/calendar").json()
    crude = next(e for e in d["events"] if e["title"] == "Crude Oil Inventories")
    # the fixture's newest EIA week is 2026-10-30; this release reports the week ending 2026-11-06
    assert crude["actual"] is None and "does not show the 2026-11-06 observation yet" in crude["actual_note"]
    assert up.calls["eia_series"] == 1
    spec = actuals.SPECS["Crude Oil Inventories"]
    obs = [{"date": "2026-10-30", "value": 409760.0}, {"date": "2026-11-06", "value": 406260.0}]
    assert actuals.compute(spec, crude["t"], obs) == {"actual": "-3.5M", "actual_period": "2026-11-06", "derived": True}
    # last week's release against the same series: the 10-30 week IS the expected one
    assert actuals.compute(spec, utc(2026, 11, 4, 15, 30), eia.parse_series(up._eia_series("PET.WCESTUS1.W", 5))) == \
           {"actual": "-0.9M", "actual_period": "2026-10-30", "derived": True}


def test_actuals_never_show_an_older_period_as_the_new_release():
    b = bls.parse_many(fx("bls_calendar.json"))
    cpi, ppi = actuals.SPECS["CPI m/m"], actuals.SPECS["PPI m/m"]
    assert actuals.expected_period(cpi, utc(2026, 11, 10, 13, 30)) == "2026-10"
    assert actuals.expected_period(cpi, utc(2027, 1, 13, 13, 30)) == "2026-12"
    assert actuals.compute(cpi, utc(2026, 11, 10, 13, 30), b["CUSR0000SA0"])["actual"] == "0.4%"
    assert actuals.compute(cpi, utc(2026, 12, 10, 13, 30), b["CUSR0000SA0"]) is None      # November CPI not out
    assert actuals.compute(ppi, utc(2026, 11, 13, 13, 30), b["WPSFD4"]) is None           # series still at September
    assert actuals.compute(actuals.SPECS["CPI y/y"], utc(2026, 11, 10, 13, 30), b["CUUR0000SA0"]) is None  # BLS gives no 12-month figure
    assert actuals.compute(actuals.SPECS["Unemployment Rate"], utc(2026, 11, 6, 13, 30), b["LNS14000000"])["actual"] == "4.1%"
    assert actuals.compute(actuals.SPECS["Non-Farm Employment Change"], utc(2026, 11, 6, 13, 30), b["CES0000000001"])["actual"] == "165K"
    claims = actuals.SPECS["Unemployment Claims"]
    icsa = fred.parse_observations(fx("fred_ICSA.json"))
    assert actuals.expected_period(claims, utc(2026, 11, 12, 13, 30)) == "2026-11-07"     # the Saturday before
    assert actuals.expected_period(claims, utc(2026, 11, 14, 13, 30)) == "2026-11-07"     # released ON a Saturday: the one before
    assert actuals.compute(claims, utc(2026, 11, 19, 13, 30), icsa) is None
    retail = fred.parse_observations(fx("fred_RSAFS.json"))
    assert actuals.compute(actuals.SPECS["Retail Sales m/m"], utc(2026, 10, 16, 12, 30), retail) == \
           {"actual": "0.8%", "actual_period": "2026-09", "derived": True}
    assert actuals.compute(actuals.SPECS["Retail Sales m/m"], utc(2026, 11, 17, 13, 30), retail) is None
    assert actuals.expected_period(actuals.SPECS["Federal Funds Rate"], utc(2026, 12, 16, 19, 0)) == "2026-12-17"
    assert actuals.spec_for({"currency": "EUR", "title": "CPI m/m"}) is None
    assert actuals._fmt(-0.04, 1) == "0.0"


def test_calendar_survives_a_failing_actual_source_and_says_which(client, up, clock):
    up.down.add("bls")
    d = client.get("/api/calendar").json()
    ev = {e["title"]: e for e in d["events"] if e["currency"] == "USD"}
    assert ev["CPI m/m"]["actual"] is None and "BLS CUSR0000SA0 could not be read" in ev["CPI m/m"]["actual_note"]
    assert ev["Unemployment Claims"]["actual"] == "200K"
    assert d["actuals"]["sources"]["bls"]["ok"] is False and "service unavailable" in d["actuals"]["sources"]["bls"]["error"]


def test_bls_outage_after_a_restart_keeps_released_figures_and_error_text_is_readable(client, up, clock, monkeypatch):
    d = client.get("/api/calendar").json()                           # BLS answers: the answer is kept on disk
    assert {e["title"]: e for e in d["events"]}["CPI m/m"]["actual"] == "0.4%"
    assert diskstore.load(macro.BLS_STORE)["value"]["series"]["CUSR0000SA0"]
    cache.clear()                                                    # restart, and BLS answers with its "busy" page

    def busy(ids, *a, **k):
        raise UpstreamError("bls", 503, 'HTTP 503 Service Unavailable — The BLS website is busy '
                            '(window.BOOMR_mq=window.BOOMR_mq||[]).push(["addVar",{"rua.upush":"false"')
    monkeypatch.setattr(bls, "fetch_many", busy)
    d = client.get("/api/calendar").json()
    ev = {e["title"]: e for e in d["events"] if e["currency"] == "USD"}
    assert (ev["CPI m/m"]["actual"], ev["CPI m/m"]["actual_source"], ev["CPI m/m"]["actual_period"]) == \
           ("0.4%", "BLS CUSR0000SA0", "2026-10")
    src = d["actuals"]["sources"]["bls"]
    assert src["ok"] is False and src["as_of"] and src["error"].endswith("The BLS website is busy")
    assert "window" not in json.dumps(d) and "BOOMR" not in json.dumps(d)
    # the kept answer is never stretched to a later release: PPI (series still at September) stays empty and says why
    clock.now = utc(2026, 11, 13, 14, 0)
    cache.clear()
    d = client.get("/api/calendar").json()
    ppi = next(e for e in d["events"] if e["title"] == "PPI m/m" and e["currency"] == "USD")
    assert ppi["actual"] is None and "could not be refreshed" in ppi["actual_note"] and "2026-10" in ppi["actual_note"]
    assert macro._tidy("HTTP 429 Too Many Requests — Rate Limited html { height: 100%; overflow-y: scroll") == \
           "HTTP 429 Too Many Requests — Rate Limited"
    assert macro._tidy("plain sentence (with a bracket)") == "plain sentence (with a bracket)"
    assert macro._tidy(None) == ""


def test_calendar_host_rate_limit_is_respected_and_the_disk_copy_is_served(client, up, clock, monkeypatch):
    assert client.get("/api/calendar").status_code == 200           # writes the disk copy
    assert diskstore.load(macro.CALENDAR_STORE)["value"]["events"]
    cache.clear()                                                    # restart…
    clock.tick(macro.CALENDAR_TTL + 60)                              # …after the copy on disk is due for a refresh
    calls = {"n": 0}

    def limited(url, headers=None, timeout=10, source=None):
        calls["n"] += 1
        raise RateLimited(source, "HTTP 429 Too Many Requests", retry_after=None)
    monkeypatch.setattr(http, "get_json", limited)
    monkeypatch.setattr(forexfactory, "fetch", _REAL["forexfactory.fetch"])      # the real fetch with its back-off
    d = client.get("/api/calendar").json()
    assert d["stale"] is True and d["error"]["code"] == "rate_limited" and d["error"]["retry_after"] == forexfactory.BACKOFF
    assert len(d["events"]) == 106 and calls["n"] == 1
    for _ in range(5):                                               # however often the page asks…
        clock.tick(60)
        assert client.get("/api/calendar").json()["stale"] is True
    assert calls["n"] == 1, "…the host is not asked again inside the back-off"
    clock.tick(forexfactory.BACKOFF)
    client.get("/api/calendar")
    assert calls["n"] == 2
    # no memory, no usable disk copy -> the typed 429 with Retry-After
    cache.clear()
    diskstore.save(macro.CALENDAR_STORE, {"events": [{"t": clock.now - 9 * 86400}]})      # last week's file
    r = client.get("/api/calendar")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) > 0


def test_calendar_is_asked_at_most_every_two_hours_even_across_restarts(client, up, clock):
    for _ in range(3):
        client.get("/api/calendar")
        clock.tick(1800)
    assert up.calls["forexfactory"] == 1 and macro.CALENDAR_TTL >= 3600
    cache.clear()                                               # = a gateway restart, 90 minutes after the fetch
    d = client.get("/api/calendar").json()
    assert up.calls["forexfactory"] == 1, "a restart inside the two hours must not ask the host again"
    assert d["as_of"] == RECORDED_AT and "stale" not in d and len(d["events"]) == 106     # the age shown is the real one
    clock.tick(macro.CALENDAR_TTL)
    client.get("/api/calendar")
    assert up.calls["forexfactory"] == 2


def test_bls_key_travels_in_the_post_body_and_the_helper_keeps_its_contract(monkeypatch):
    seen = {}
    monkeypatch.setattr(http, "post_json", lambda url, payload, headers=None, timeout=10, source=None:
                        seen.update(url=url, payload=payload) or fx("bls_calendar.json"))
    monkeypatch.setenv("BLS_API_KEY", "SECRET-BLS")
    got = bls.parse_many(bls.fetch_many(["CUSR0000SA0"]))
    assert "SECRET-BLS" not in seen["url"] and seen["payload"]["registrationkey"] == "SECRET-BLS"
    assert seen["payload"]["calculations"] is True
    row = got["CUSR0000SA0"][0]
    assert (row["period"], row["value"], row["pct"]["1"]) == ("2026-10", 337.476, 0.4)
    assert got["CES0000000001"][0]["net"]["1"] == 165.0 and got["CES0000000001"][0]["preliminary"] is True
    pts = bls.series("WPSFD4")
    assert pts[-1] == ("2026-M09", 159.78) and pts == sorted(pts)             # newest last
    with pytest.raises(BadRequest):
        bls.fetch_many(["bad id&x"])
    with pytest.raises(UpstreamError):
        bls.parse_many({"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold reached"]})
    monkeypatch.setattr(http, "post_json", lambda *a, **k: (_ for _ in ()).throw(UpstreamError("bls", 500, "x")))
    assert bls.series("WPSFD4") == []


# ===================================================================== reference routes
def test_eia_reference_route_validates_and_returns_oldest_first(client, up):
    d = client.get("/api/eia?series_id=pet.wcestus1.w&length=3").json()
    assert d["source"] == "eia" and d["freshness"] == "WEEKLY" and d["series"] == "PET.WCESTUS1.W" and d["unit"] == "kbbl"
    assert [o["date"] for o in d["obs"]] == ["2026-10-16", "2026-10-23", "2026-10-30"] and d["obs"][-1]["value"] == 409760.0
    for bad in ("/api/eia?series_id=A%20B", "/api/eia?series_id=PET.WCESTUS1.W%26api_key%3Dx",
                "/api/eia?series_id=../../etc", "/api/eia?length=abc"):
        r = client.get(bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", bad
    assert len(client.get("/api/eia?length=99999").json()["obs"]) == 30       # clamped to what exists
    assert up.calls["eia_series"] == 2                                        # buckets 12 and 520, then cache


def test_macro_fred_route(client, up, monkeypatch):
    d = client.get("/api/macro?series_id=dcoilwtico&limit=3").json()
    assert d["source"] == "fred" and d["series"] == "DCOILWTICO"
    assert [o["date"] for o in d["obs"]] == ["2026-10-30", "2026-11-02", "2026-11-03"] and d["obs"][-1]["value"] == 99.64
    assert client.get("/api/macro?series_id=A%20B").status_code == 400
    assert client.get("/api/macro?series_id=X%26api_key%3D1").status_code == 400
    monkeypatch.setattr(fred, "fetch_observations", _REAL["fred.fetch_observations"])
    cache.clear()
    seen = {}
    monkeypatch.setenv("FRED_API_KEY", "SECRET-FRED")
    monkeypatch.setattr(http, "get_json", lambda url, headers=None, timeout=10, source=None:
                        seen.update(url=url) or (_ for _ in ()).throw(UpstreamError("fred", 400, http.scrub("bad " + url))))
    r = client.get("/api/macro?series_id=NOPE")
    assert r.status_code == 502 and "SECRET-FRED" in seen["url"] and "SECRET-FRED" not in r.text
    monkeypatch.delenv("FRED_API_KEY")
    cache.clear()
    assert client.get("/api/macro?series_id=DGS10").status_code == 503


def test_factors_monthly_table_no_longer_swallows_the_annual_rows(client, up):
    rows = french.parse(raw("french_factors_monthly.csv"))
    assert [r["date"] for r in rows] == ["1926-07", "1926-08", "1926-09", "2026-04", "2026-05", "2026-06",
                                         "2026-07", "2026-08", "2026-09"]
    assert rows[-1] == {"date": "2026-09", "Mkt-RF": -1.07, "SMB": -3.16, "HML": -1.55, "RF": 0.31}
    d = client.get("/api/factors?limit=2").json()
    assert d["source"] == "ken-french" and [r["date"] for r in d["rows"]] == ["2026-08", "2026-09"]
    assert client.get("/api/factors?dataset=../etc/passwd").status_code == 400
    with pytest.raises(UpstreamError):
        french.parse("no table here\n")


def test_worldbank_usda_and_trade_routes(client, up):
    d = client.get("/api/worldbank?country=us&code=ny.gdp.mktp.cd").json()
    assert d["source"] == "world-bank" and d["freshness"] == "ANNUAL" and d["indicator"] == "GDP (current US$)"
    assert d["rows"][-1] == {"date": "2025", "value": 30769700000000.0} and d["rows"] == sorted(d["rows"], key=lambda r: r["date"])
    for bad in ("/api/worldbank?country=A%20B", "/api/worldbank?code=NY.GDP%2F..%2Fx", "/api/worldbank?country=U1"):
        assert client.get(bad).status_code == 400, bad
    d = client.get("/api/usda?commodity=corn").json()
    assert d["source"] == "usda-nass" and len(d["rows"]) == 7
    assert all(r["value"] is None or isinstance(r["value"], float) for r in d["rows"])
    assert any(r["unit"] == "BU" and r["value"] and r["value"] > 1e9 for r in d["rows"])
    assert client.get("/api/usda?commodity=CORN%26key%3Dx").status_code == 400
    assert client.get("/api/usda?year=20x4").status_code == 400
    d = client.get("/api/trade?year=2024").json()
    assert d["source"] == "un-comtrade" and d["year"] == 2024 and d["rows"][0]["name"] == "Canada"
    for bad in ("/api/trade?reporter=all", "/api/trade?flow=Q", "/api/trade?year=20", "/api/trade?commodity=27%2009"):
        assert client.get(bad).status_code == 400, bad


def test_retired_routes_are_gone_not_left_returning_errors(client):
    for path in ("/api/imf", "/api/eurostat", "/api/oecd", "/api/rigcount", "/api/equity_quote", "/api/sentinel",
                 "/api/grade"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_source", path
    assert "macro" not in gateway.QUARANTINED
    paths = {r.path for r in macro.router.routes}
    assert paths == {"/api/supply/oil", "/api/news", "/api/calendar", "/api/eia", "/api/macro", "/api/trade",
                     "/api/filings", "/api/factors", "/api/worldbank", "/api/usda"}


# ===================================================================== hygiene
S6_PROVIDERS = ("actuals", "bls", "census", "comtrade", "courtlistener", "datagov", "diskstore", "dol", "edgar", "eia",
                "firms", "fmp", "forexfactory", "fred", "french", "gdelt", "laborcurrent", "marketaux", "news",
                "openfda", "usda", "worldbank")


def test_slice_code_uses_core_http_has_no_personal_address_and_plain_def_routes():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [os.path.join(here, "providers", n + ".py") for n in S6_PROVIDERS] + [os.path.join(here, "routes", "macro.py")]
    for path in files:
        src = open(path, encoding="utf-8").read()
        assert "urllib.request" not in src and "urlopen" not in src, path
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", src), f"an e-mail address in {path}"
        assert "numpy" not in src and "pandas" not in src and "import requests" not in src, path
    for name in ("bakerhughes", "equities_fallback", "intl_macro"):
        assert not os.path.exists(os.path.join(here, "providers", name + ".py")), name
    src = open(os.path.join(here, "routes", "macro.py"), encoding="utf-8").read()
    assert "async def" not in src and "sys.path" not in src and "lse.economic_calendar" not in src
    for route in macro.router.routes:
        assert not inspect.iscoroutinefunction(route.endpoint), route.path


def test_keys_travel_in_headers_where_the_api_accepts_one(monkeypatch):
    """FMP (`apikey` header), openFDA (basic auth), Regulations.gov (X-Api-Key), LaborCurrent, CourtListener:
    verified against the real services on 2026-11-12. The URL must never hold the key."""
    from providers import courtlistener, datagov, fmp, laborcurrent, openfda
    seen = []
    monkeypatch.setattr(http, "get_json", lambda url, headers=None, timeout=10, source=None:
                        seen.append((url, headers or {})) or {})
    for name in ("FMP_API_KEY", "OPENFDA_API_KEY", "DATA_GOV_API_KEY", "LABORCURRENT_API_KEY", "COURTLISTENER_API_TOKEN"):
        monkeypatch.setenv(name, "SECRET-" + name)
    fmp.earnings_calendar(3)
    openfda.drug_shortages(1)
    datagov.regulations_recent(limit=5)
    laborcurrent.recent_warn(1)
    courtlistener.recent_suits("Boeing", limit=1)
    assert len(seen) == 5
    for url, headers in seen:
        assert "SECRET-" not in url, url
        assert any("SECRET-" in v or v.startswith("Basic ") for v in headers.values()), (url, list(headers))
    assert seen[0][1] == {"apikey": "SECRET-FMP_API_KEY"} and seen[2][1] == {"X-Api-Key": "SECRET-DATA_GOV_API_KEY"}
    import base64
    assert base64.b64decode(seen[1][1]["Authorization"].split()[1]).decode() == "SECRET-OPENFDA_API_KEY:"


def test_diskstore_roundtrip_and_name_safety(tmp_path):
    assert diskstore.load("nothing_here") is None
    assert diskstore.save("a_b-1", {"x": [1, 2]}) is True
    got = diskstore.load("a_b-1")
    assert got["value"] == {"x": [1, 2]} and abs(got["at"] - time.time()) < 5
    assert open(os.path.join(diskstore.DIR, ".gitignore")).read().strip() == "*"
    for bad in ("../x", "a:b", "A", "x" * 65, "a/b", ""):
        with pytest.raises(ValueError):
            diskstore.save(bad, 1)
    open(os.path.join(diskstore.DIR, "broken.json"), "w").write("{not json")
    assert diskstore.load("broken") is None
