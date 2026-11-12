"""Options tests (S3): the pricing maths, the Deribit and CBOE parsers on recorded fixtures, the
cached chain, and the four routes. Network-free: both provider fetch functions are replaced by
fixtures recorded from the real sources on 2026-11-12 13:23 UTC (rows dropped to trim them, values
untouched) in tests/fixtures/s3/. The clock is pinned to the recording time, so the fixtures'
expiries stay in the future however late the suite runs.
"""
import json
import math
import os
import time

import pytest
from fastapi.testclient import TestClient

from core import cache, http, session, status
from core.errors import BadRequest, UpstreamError
from providers import cboe_options, deribit
from routes import options as R

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "s3")
RECORDED_AT = 1794489800.0            # 2026-11-12 13:23:20 UTC
CBOE_FILE = {"AAPL": "cboe_AAPL.json", "_SPX": "cboe_SPX.json", "_VIX": "cboe_VIX.json"}
DERIBIT_FILE = {"BTC": "deribit_BTC.json", "USDC": "deribit_USDC.json"}


def fx(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class Clock:
    def __init__(self, monkeypatch, start=RECORDED_AT):
        self.now = start
        monkeypatch.setattr(time, "time", lambda: self.now)

    def tick(self, seconds):
        self.now += seconds


class Upstreams:
    """Both sources served from fixtures; counts calls; can be taken down per source."""

    def __init__(self, monkeypatch):
        self.calls = {"deribit": 0, "cboe": 0}
        self.down = set()
        monkeypatch.setattr(deribit, "fetch_summary", self._deribit)
        monkeypatch.setattr(cboe_options, "fetch_chain", self._cboe)

    def _deribit(self, currency, **k):
        self.calls["deribit"] += 1
        if "deribit" in self.down:
            raise UpstreamError("deribit", 503, "HTTP 503 Service Unavailable")
        return fx(DERIBIT_FILE[currency])

    def _cboe(self, symbol, **k):
        self.calls["cboe"] += 1
        if "cboe" in self.down:
            raise UpstreamError("cboe", None, "no answer within 40 s")
        return fx(CBOE_FILE[symbol])


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    status.reset()
    yield
    cache.clear()
    status.reset()


@pytest.fixture()
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture()
def up(monkeypatch):
    return Upstreams(monkeypatch)


@pytest.fixture()
def api(up, clock):
    import app as gateway
    return TestClient(gateway.app, raise_server_exceptions=False)


def chain_of(kind, symbol, fetched_at=RECORDED_AT):
    if kind == "deribit":
        snap = deribit.parse_summary(fx(DERIBIT_FILE[deribit.market(symbol)[0]]), symbol)
    else:
        snap = cboe_options.parse_chain(fx(CBOE_FILE[symbol]), symbol, eastern_to_epoch=R._eastern,
                                        expiry_epoch=R._expiry_epoch)
    return R.build_chain(snap, fetched_at=fetched_at)


# ============================================================ fixtures hygiene
def test_fixtures_carry_no_key_material():
    names = sorted(os.listdir(FIXTURES))
    assert len(names) >= 6
    for name in names:
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            text = f.read()
        assert http.scrub(text) == text, f"{name} contains something that looks like a key"
        for marker in ("token=", "apikey=", "api_key=", "api_token=", "@"):
            assert marker not in text, (name, marker)


# ============================================================ pricing maths
def test_normal_distribution():
    assert R.norm_cdf(0) == pytest.approx(0.5, abs=1e-15)
    assert R.norm_cdf(1.96) == pytest.approx(0.9750021, abs=1e-7)
    assert R.norm_cdf(-1.0) == pytest.approx(0.1586553, abs=1e-7)
    assert R.norm_pdf(0) == pytest.approx(0.3989423, abs=1e-7)
    assert R.norm_pdf(1.5) == pytest.approx(0.1295176, abs=1e-7)


def test_black76_textbook_price():
    # Hull, "Options, Futures, and Other Derivatives": European put on a crude-oil future,
    # F = 20, K = 20, r = 9 %, T = 4 months, sigma = 25 %  ->  p = 1.12
    put = R.black76(20.0, 20.0, 4.0 / 12.0, 0.25, "P", r=0.09)
    assert put["price"] == pytest.approx(1.12, abs=0.005)
    call = R.black76(20.0, 20.0, 4.0 / 12.0, 0.25, "C", r=0.09)
    assert call["price"] == pytest.approx(put["price"], abs=1e-12)          # at the money forward: C = P
    # hand calculation: d1 = 0.25*sqrt(1/3)/2 = 0.072169, N(d1) - N(-d1) = 0.057533
    assert put["price"] == pytest.approx(math.exp(-0.03) * 20 * 0.057533, abs=2e-5)


@pytest.mark.parametrize("f,k,t,iv,r", [
    (72613.0, 74000.0, 43 / 365, 0.37, 0.0), (72613.0, 60000.0, 0.5, 0.45, 0.0),
    (355.6, 300.0, 64 / 365, 0.24, 0.045), (81.6, 95.0, 0.04, 0.57, 0.0), (0.6512, 0.66, 0.25, 0.09, 0.03),
])
def test_black76_put_call_parity(f, k, t, iv, r):
    c, p = R.black76(f, k, t, iv, "C", r), R.black76(f, k, t, iv, "P", r)
    df = math.exp(-r * t)
    assert c["price"] - p["price"] == pytest.approx(df * (f - k), rel=1e-10, abs=1e-10)
    assert c["delta"] - p["delta"] == pytest.approx(df, abs=1e-12)          # parity of the deltas
    assert c["gamma"] == pytest.approx(p["gamma"], rel=1e-12)
    assert c["vega"] == pytest.approx(p["vega"], rel=1e-12)
    assert c["price"] >= max(0.0, df * (f - k)) and p["price"] >= max(0.0, df * (k - f))


@pytest.mark.parametrize("cp", ["C", "P"])
def test_black76_greeks_are_the_derivatives_of_the_price(cp):
    f, k, t, iv, r = 73000.0, 74000.0, 43.0 / 365.0, 0.3734, 0.02
    g = R.black76(f, k, t, iv, cp, r)

    def px(f=f, t=t, iv=iv):
        return R.black76(f, k, t, iv, cp, r)["price"]
    h = 1.0
    assert g["delta"] == pytest.approx((px(f=f + h) - px(f=f - h)) / (2 * h), rel=1e-6)
    assert g["gamma"] == pytest.approx((px(f=f + h) - 2 * px() + px(f=f - h)) / (h * h), rel=1e-4)
    assert g["vega"] == pytest.approx((px(iv=iv + 0.005) - px(iv=iv - 0.005)), rel=1e-5)      # per 1 vol point
    day = 1.0 / 365.0
    assert g["theta"] == pytest.approx((px(t=t - day / 2) - px(t=t + day / 2)), rel=1e-4)     # per calendar day


@pytest.mark.parametrize("fwd", [0.6512, 2.57, 72.4, 355.6, 7575.0, 72613.0])
def test_strikes_are_absolute_prices_at_every_price_level(fwd):
    """The old GREEKS page called bs(S, k*S, …): a strike that was already a price got multiplied by
    spot again, so CL (72) produced deltas of 1e-84 and AUDUSD (0.65) deltas of 1.00. With absolute
    strikes an at-the-money option has a delta near one half whatever the price level is."""
    atm = R.black76(fwd, fwd, 30 / 365, 0.30, "C")
    assert 0.50 < atm["delta"] < 0.53
    assert R.black76(fwd, fwd, 30 / 365, 0.30, "P")["delta"] == pytest.approx(atm["delta"] - 1.0, abs=1e-12)
    # 5 % out of the money: d1 = (ln(1/1.05) + 0.086^2/2) / 0.086 = -0.5243, N(d1) = 0.3000
    assert R.black76(fwd, fwd * 1.05, 30 / 365, 0.30, "C")["delta"] == pytest.approx(0.3000, abs=5e-4)
    # the scale-free quantities do not depend on the price level; the others scale with it
    ref = R.black76(100.0, 100.0, 30 / 365, 0.30, "C")
    assert atm["gamma"] * fwd == pytest.approx(ref["gamma"] * 100.0, rel=1e-12)
    assert atm["vega"] / fwd == pytest.approx(ref["vega"] / 100.0, rel=1e-12)
    assert atm["theta"] / fwd == pytest.approx(ref["theta"] / 100.0, rel=1e-12)
    # what the old bug computed is far from the money and visibly different
    if fwd > 1.5:
        assert R.black76(fwd, fwd * fwd, 30 / 365, 0.30, "C")["delta"] < 1e-6
    elif fwd < 0.9:
        assert R.black76(fwd, fwd * fwd, 30 / 365, 0.30, "C")["delta"] > 0.99


def test_black76_refuses_unusable_inputs():
    ok = (100.0, 100.0, 0.1, 0.3, "C")
    assert R.black76(*ok) is not None
    for bad in [(0.0, 100.0, 0.1, 0.3, "C"), (100.0, 0.0, 0.1, 0.3, "C"), (100.0, -5.0, 0.1, 0.3, "C"),
                (100.0, 100.0, 0.0, 0.3, "C"), (100.0, 100.0, -1.0, 0.3, "C"), (100.0, 100.0, 0.1, 0.0, "C"),
                (100.0, 100.0, 0.1, 0.3, "X"), (float("nan"), 100.0, 0.1, 0.3, "C"),
                (100.0, float("inf"), 0.1, 0.3, "C"), (None, 100.0, 0.1, 0.3, "C"), ("abc", 100.0, 0.1, 0.3, "C"),
                (100.0, 100.0, 30.0 / R.YEAR, 0.3, "C")]:                    # 30 s to expiry
        assert R.black76(*bad) is None, bad


def test_black76_matches_deribits_own_greeks_and_mark_price():
    """Deribit's `ticker` greeks (recorded for five contracts) against Black-76 on the same ticker's
    mark IV, forward and timestamp, r = 0. Deribit rounds delta and gamma to 5 decimals."""
    ticks = fx("deribit_tickers.json")
    assert len(ticks) == 5
    for tk in ticks:
        base, _, exp_ts, strike, cp = deribit.parse_name(tk["instrument_name"])
        t = (exp_ts - tk["timestamp"] / 1000.0) / R.YEAR
        fwd = tk["underlying_price"]
        g = R.black76(fwd, strike, t, tk["mark_iv"] / 100.0, cp)
        src = tk["greeks"]
        name = tk["instrument_name"]
        assert g["delta"] == pytest.approx(src["delta"], abs=6e-4), name     # mark_iv itself is rounded to 0.01
        assert g["gamma"] == pytest.approx(src["gamma"], abs=6e-6 if fwd > 1000 else 2e-4), name
        assert g["vega"] == pytest.approx(src["vega"], rel=2e-3), name
        assert g["theta"] == pytest.approx(src["theta"], rel=3e-3), name
        # the mark price: in coin for BTC/ETH (price / forward), in USDC for SOL
        model = g["price"] / fwd if base in ("BTC", "ETH") else g["price"]
        assert model == pytest.approx(tk["mark_price"], rel=4e-3, abs=6e-5), name


def test_gex_unit_by_hand():
    # gamma 0.05 per $1, 10 contracts x 100 shares, price 100:  0.05 x 1000 x 100^2 x 1 % = $5,000 = 0.005 $mm
    assert R.gex_usd_mm(0.05, 10 * 100, 100.0) == pytest.approx(0.005, rel=1e-12)
    # 1 BTC of open interest, gamma 4e-5 per $1, forward 73,000:  4e-5 x 73000^2 x 1 % = $2,131.6
    assert R.gex_usd_mm(4e-5, 1.0, 73000.0) == pytest.approx(0.0021316, rel=1e-9)


# ============================================================ provider parsers
def test_deribit_instrument_names():
    assert deribit.parse_name("BTC-25DEC26-74000-C") == ("BTC", "2026-12-25", 1798185600.0, 74000.0, "C")
    assert deribit.parse_name("BTC-4DEC26-74000-P")[1:] == ("2026-12-04", 1796371200.0, 74000.0, "P")
    assert deribit.parse_name("SOL_USDC-25DEC26-90-C")[0] == "SOL_USDC"
    assert deribit.parse_name("XRP_USDC-27NOV26-0d55-P")[3] == pytest.approx(0.55)
    assert time.gmtime(deribit.parse_name("ETH-26MAR27-2000-C")[2])[:5] == (2027, 3, 26, 8, 0)   # 08:00 UTC
    for junk in ("BTC-PERPETUAL", "BTC-25DEC26", "BTC-31FEB27-100-C", "BTC-25XXX26-100-C", "BTC-25DEC26-0-C",
                 "BTC-25DEC26-100-X", "", None, "btc-25DEC26-100-C"):
        assert deribit.parse_name(junk) is None, junk


def test_deribit_market_is_a_whitelist():
    assert deribit.market("BTC") == ("BTC", "BTC-", "BTC")
    assert deribit.market("SOL_USDC") == ("USDC", "SOL_USDC-", "USDC")
    for bad in ("DOGE", "btc", "BTC&kind=future", "", None):
        with pytest.raises(BadRequest):
            deribit.market(bad)
    with pytest.raises(BadRequest):
        deribit.fetch_summary("BTC&x=1")                    # never reaches the network


def test_deribit_summary_parser_converts_coin_prices_with_the_rows_own_forward():
    raw = fx("deribit_BTC.json")
    snap = deribit.parse_summary(raw, "BTC")
    assert snap["source"] == "deribit" and snap["native"] == "BTC"
    assert snap["as_of"] == pytest.approx(raw["usOut"] / 1e6) and abs(snap["as_of"] - RECORDED_AT) < 10
    assert snap["spot"] == 72613.26                          # Deribit index (estimated_delivery_price)
    assert len(snap["contracts"]) == len(raw["result"]) == 268
    assert sorted({c["date"] for c in snap["contracts"]}) == ["2026-11-13", "2026-11-27", "2026-12-25"]
    by_name = {r["instrument_name"]: r for r in raw["result"]}
    for c in snap["contracts"]:
        r = by_name[c["name"]]
        assert c["fwd"] == r["underlying_price"] and c["iv"] == pytest.approx(r["mark_iv"] / 100.0)
        assert c["mark"] == pytest.approx(r["mark_price"] * r["underlying_price"])
        assert c["mark_native"] == r["mark_price"] and c["ask_native"] == r["ask_price"]
        assert (c["bid"] is None) == (r["bid_price"] is None)             # no bid stays "no bid", not 0
        assert c["oi"] == r["open_interest"] and c["delta"] is None       # the summary has no greeks
    sample = next(c for c in snap["contracts"] if c["name"] == "BTC-13NOV26-84000-P")
    assert sample["strike"] == 84000.0 and sample["type"] == "P" and sample["exp_ts"] == 1794556800.0
    assert sample["mark"] == pytest.approx(0.15664357 * 72625.67) and sample["bid"] == pytest.approx(0.1545 * 72625.67)


def test_deribit_usdc_answer_is_filtered_to_sol_and_not_converted():
    raw = fx("deribit_USDC.json")
    others = {r["instrument_name"].split("-")[0] for r in raw["result"]} - {"SOL_USDC"}
    assert others >= {"BTC_USDC", "ETH_USDC", "XRP_USDC"}                 # the answer mixes underlyings
    snap = deribit.parse_summary(raw, "SOL_USDC")
    assert snap["native"] == "USDC" and snap["spot"] == 81.5786
    assert snap["contracts"] and all(c["name"].startswith("SOL_USDC-") for c in snap["contracts"])
    c = next(c for c in snap["contracts"] if c["name"] == "SOL_USDC-25DEC26-92-P")
    assert c["mark"] == c["mark_native"] == 12.79206647 and c["oi"] == 3890.0 and c["fwd"] == 82.0808
    src = next(r for r in raw["result"] if r["instrument_name"] == c["name"])
    assert c["iv"] == pytest.approx(src["mark_iv"] / 100.0) and 0.3 < c["iv"] < 1.0


def test_deribit_parser_rejects_a_reply_without_a_result():
    with pytest.raises(UpstreamError) as e:
        deribit.parse_summary({"jsonrpc": "2.0", "error": {"message": "Invalid params", "code": -32602}}, "BTC")
    assert "Invalid params" in str(e.value)
    with pytest.raises(UpstreamError):
        deribit.parse_summary([], "BTC")
    assert deribit.parse_summary({"result": [{"instrument_name": "BTC-PERPETUAL"}, "junk", {}]}, "BTC")["contracts"] == []


def test_cboe_occ_symbols():
    assert cboe_options.parse_occ("AAPL261120C00355000") == ("AAPL", "2026-11-20", "C", 355.0)
    assert cboe_options.parse_occ("SPXW261120P07570000") == ("SPXW", "2026-11-20", "P", 7570.0)
    assert cboe_options.parse_occ("VIX261118C00018500") == ("VIX", "2026-11-18", "C", 18.5)
    assert cboe_options.parse_occ("BRKB270115C00500000")[0] == "BRKB"
    for junk in ("AAPL", "AAPL261320C00355000", "AAPL270231C00355000", "AAPL261120X00355000", "AAPL261120C00000000", "", None,
                 "aapl261120C00355000"):
        assert cboe_options.parse_occ(junk) is None, junk
    with pytest.raises(BadRequest):
        cboe_options.fetch_chain("../quotes/AAPL")           # never reaches the network
    with pytest.raises(BadRequest):
        cboe_options.fetch_chain("AAPL?x=1")


def test_cboe_chain_parser_keeps_zero_as_missing():
    raw = fx("cboe_AAPL.json")
    snap = cboe_options.parse_chain(raw, "AAPL", eastern_to_epoch=R._eastern, expiry_epoch=R._expiry_epoch)
    assert snap["source"] == "cboe" and snap["native"] == "USD" and snap["spot"] == 355.57
    assert time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(snap["as_of"])) == raw["timestamp"] == "2026-11-12 11:01:30"
    assert len(snap["contracts"]) == len(raw["data"]["options"])
    by_name = {o["option"]: o for o in raw["data"]["options"]}
    n_no_iv = 0
    for c in snap["contracts"]:
        o = by_name[c["name"]]
        assert c["oi"] == o["open_interest"] and c["volume"] == o["volume"]
        assert c["bid"] == (o["bid"] or None) and c["ask"] == (o["ask"] or None)
        if o["iv"] == 0:
            n_no_iv += 1
            assert c["iv"] is None and c["delta"] is None and c["gamma"] is None    # CBOE's placeholders are not greeks
        else:
            assert c["iv"] == o["iv"] and c["delta"] == o["delta"] and c["gamma"] == o["gamma"]
            assert c["vega"] == o["vega"] and c["theta"] == o["theta"]
        if o["bid"] and o["ask"]:
            assert c["mark"] == pytest.approx((o["bid"] + o["ask"]) / 2)
        else:
            assert c["mark"] is None
    assert n_no_iv >= 5
    c = next(c for c in snap["contracts"] if c["name"] == "AAPL270115C00420000")
    assert (c["key"], c["root"], c["strike"], c["type"]) == ("2027-01-15", "AAPL", 420.0, "C")
    assert (c["bid"], c["ask"], c["iv"], c["oi"], c["delta"]) == (0.41, 0.5, 0.207, 13517.0, 0.0385)
    # 16:00 New York on 15 Jan 2027 = 21:00 UTC; last trade 2026-11-11 15:53:07 New York = 20:53:07 UTC
    assert time.gmtime(c["exp_ts"])[:5] == (2027, 1, 15, 21, 0)
    assert time.gmtime(c["last_t"])[:6] == (2026, 11, 11, 20, 53, 7)


def test_cboe_series_is_date_plus_root_and_am_settled_roots_expire_in_the_morning():
    snap = cboe_options.parse_chain(fx("cboe_SPX.json"), "_SPX", eastern_to_epoch=R._eastern,
                                    expiry_epoch=R._expiry_epoch)
    keys = {c["key"] for c in snap["contracts"]}
    assert keys == {"2026-11-20.SPX", "2026-11-20.SPXW"}                  # two contracts per strike that day
    am = next(c for c in snap["contracts"] if c["root"] == "SPX")
    pm = next(c for c in snap["contracts"] if c["root"] == "SPXW")
    assert time.gmtime(am["exp_ts"])[:5] == (2026, 11, 20, 14, 30)        # 09:30 New York (EST)
    assert time.gmtime(pm["exp_ts"])[:5] == (2026, 11, 20, 21, 0)         # 16:00 New York
    vix = cboe_options.parse_chain(fx("cboe_VIX.json"), "_VIX", eastern_to_epoch=R._eastern,
                                   expiry_epoch=R._expiry_epoch)
    assert {c["key"] for c in vix["contracts"]} == {"2026-11-18"} and vix["contracts"][0]["am_settled"]
    with pytest.raises(UpstreamError):
        cboe_options.parse_chain({"data": {"symbol": "AAPL"}}, "AAPL")


# ============================================================ the chain
def test_chain_drops_expired_series_and_keeps_cboe_greeks():
    raw = fx("cboe_AAPL.json")
    expired = sum(1 for o in raw["data"]["options"] if o["option"].startswith("AAPL261111"))
    assert expired > 20                                                    # CBOE still lists yesterday's expiry
    ch = chain_of("cboe", "AAPL")
    assert [s["key"] for s in ch["series"]] == ["2026-11-20", "2027-01-15"]
    assert ch["expired_dropped"] == expired and ch["n_contracts"] == len(raw["data"]["options"]) - expired
    assert ch["n_derived"] == 0 and ch["greeks"]["origin"] == "source" and ch["mult"] == 100.0
    assert all(not r.derived for s in ch["series"] for r in s["rows"])
    front = ch["by_key"]["2026-11-20"]
    assert front["fwd_kind"] == "parity" and front["fwd"] == pytest.approx(355.775, abs=1e-6)
    assert front["atm_strike"] == 355.0 and front["atm_iv"] == pytest.approx(0.2261, abs=5e-4)
    # the published price and the front forward agree: the published price is the level
    assert ch["spot"] == 355.57 and ch["spot_kind"] == "published" and ch["published"] is None
    # rows are sorted by strike, call before put
    ks = [(r.k, r.cp) for r in front["rows"]]
    assert ks == sorted(ks)


def test_chain_derives_deribit_greeks_from_each_rows_own_iv_and_forward():
    raw = {r["instrument_name"]: r for r in fx("deribit_BTC.json")["result"]}
    ch = chain_of("deribit", "BTC")
    assert [s["key"] for s in ch["series"]] == ["2026-11-13", "2026-11-27", "2026-12-25"]
    assert ch["n_derived"] == ch["n_contracts"] == 268 and ch["n_source"] == 0
    assert ch["greeks"]["origin"] == "derived" and "Black-76" in ch["greeks"]["model"]
    assert ch["spot"] == 72613.26 and ch["spot_kind"] == "published" and ch["mult"] == 1.0 and ch["oi_unit"] == "BTC"
    dec = ch["by_key"]["2026-12-25"]
    assert dec["fwd_kind"] == "source" and 73112.0 < dec["fwd"] < 73115.0
    assert dec["atm_strike"] == 73000.0 and 0.36 < dec["atm_iv"] < 0.39
    r = next(r for r in dec["rows"] if r.name == "BTC-25DEC26-74000-C")
    src = raw[r.name]
    t = (1798185600.0 - ch["as_of"]) / R.YEAR                              # 25 Dec 2026 08:00 UTC
    want = R.black76(src["underlying_price"], 74000.0, t, src["mark_iv"] / 100.0, "C")
    assert r.derived and r.delta == want["delta"] and r.gamma == want["gamma"] and r.theta == want["theta"]
    assert 0.47 < r.delta < 0.50 and r.gamma == pytest.approx(4e-5, abs=6e-6)         # Deribit's ticker said 0.483 / 4e-5
    # Black-76 on the row's inputs reproduces the row's own USD mark (it is how Deribit builds the mark)
    assert want["price"] == pytest.approx(r.mark, rel=2e-3)
    # put-call parity holds on the REAL marks of this strike: C - P = F - K
    put = next(x for x in dec["rows"] if x.k == 74000.0 and x.cp == "P")
    assert r.mark - put.mark == pytest.approx(src["underlying_price"] - 74000.0, abs=25.0)


def test_implied_level_replaces_a_cash_index_price_the_options_are_not_priced_at():
    """Recorded at 13:23 UTC: the SPX cash index was closed at 7,619.38 while its options traded in
    global hours around 7,575 (CBOE's own deltas are 0.50 there)."""
    raw = fx("cboe_SPX.json")
    assert raw["data"]["current_price"] == 7619.3799
    ch = chain_of("cboe", "_SPX")
    assert ch["spot_kind"] == "implied" and ch["published"] == 7619.3799
    assert 7570.0 < ch["spot"] < 7585.0 and "put-call parity" in ch["spot_src"]
    s = ch["by_key"]["2026-11-20.SPXW"]
    atm_call = next(r for r in s["rows"] if r.k == s["atm_strike"] and r.cp == "C")
    assert 0.45 < atm_call.delta < 0.55, "CBOE's delta at the implied ATM strike is one half"
    far = next(r for r in s["rows"] if r.k == 7620.0 and r.cp == "C")
    assert far.delta < atm_call.delta - 0.05, "at the stale cash price the options are already out of the money"
    # VIX options price off the future of their expiry: same mechanism
    vix = chain_of("cboe", "_VIX")
    assert vix["spot_kind"] == "implied" and vix["published"] == 18.6 and 18.6 < vix["spot"] < 19.0


def test_parity_forward_and_interpolation_helpers():
    def row(k, cp, bid, ask):
        r = R.Row()
        r.k, r.cp, r.bid, r.ask = k, cp, bid, ask
        return r
    rows = [row(95, "C", 6.9, 7.1), row(95, "P", 1.4, 1.6), row(100, "C", 3.4, 3.6), row(100, "P", 2.9, 3.1),
            row(105, "C", 1.2, 1.4), row(105, "P", None, 6.0)]
    assert R._parity_forward(rows, 100.0) == (pytest.approx(100.5), 100)        # 100 + (3.5 - 3.0)
    assert R._parity_forward(rows[:1], 100.0) == (None, None)
    assert R._parity_forward(rows, 10.0) == (None, None)                        # absurd against the level: refused
    pts = [(90.0, 0.30), (100.0, 0.20), (110.0, 0.24)]
    assert R._interp(pts, 95.0) == pytest.approx(0.25) and R._interp(pts, 100.0) == pytest.approx(0.20)
    assert R._interp(pts, 89.0) == 0.30 and R._interp(pts, 80.0) is None        # just outside / far outside
    assert R._interp(pts, 89.0, reach=0.0) is None and R._interp([], 1.0) is None
    assert R._median([3, 1, 2]) == 2 and R._median([4, 1, 2, 3]) == 2.5 and R._median([]) is None


# ============================================================ routes
def test_options_route_lists_every_expiry(api, up):
    r = api.get("/api/options?id=btc")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == "BTC" and d["source"] == "deribit" and d["freshness"] == "~RT" and d["ttl"] == 15
    assert abs(d["as_of"] - RECORDED_AT) < 10 and d["spot"] == 72613.26 and d["currency"] == "USD"
    assert [e["key"] for e in d["expiries"]] == ["2026-11-13", "2026-11-27", "2026-12-25"]
    e = d["expiries"][2]
    assert e["date"] == "2026-12-25" and e["dte"] == pytest.approx(42.775, abs=0.01) and e["n"] == 120
    assert e["strikes"] == 60 and e["oi"] > 100000 and 0.36 < e["atm_iv"] < 0.39 and e["atm_strike"] == 73000.0
    assert sum(x["oi_pct"] for x in d["expiries"]) == pytest.approx(100.0, abs=0.01)
    assert d["totals"]["contracts"] == 268 and d["totals"]["expiries"] == 3
    assert d["default_expiry"] == "2026-11-27", "nearest expiry at least 7 days out"
    assert d["greeks"]["origin"] == "derived" and d["oi_unit"] == "BTC" and "forward" in d["price_note"]
    assert "stale" not in d


def test_chain_route_rows(api, up):
    d = api.get("/api/options/chain?id=BTC&expiry=2026-12-25").json()
    assert d["expiry"]["key"] == "2026-12-25" and d["atm_strike"] == 73000.0 and 73112 < d["fwd"] < 73115
    assert len(d["rows"]) == 120 and d["native"] == "BTC"
    need = {"strike", "type", "bid", "ask", "mark", "iv", "delta", "gamma", "vega", "theta", "oi", "volume", "derived"}
    for row in d["rows"]:
        assert need <= set(row) and row["type"] in ("C", "P") and row["derived"] is True
        assert 0.05 < row["iv"] < 3.0, "iv is a decimal, not a percentage"
        assert (0 <= row["delta"] <= 1) if row["type"] == "C" else (-1 <= row["delta"] <= 0)
        assert row["gamma"] >= 0 and row["vega"] >= 0 and row["theta"] <= 0
        assert row["mark"] > 0 and row["mark"] == pytest.approx(row["mark_native"] * d["fwd"], rel=2e-4)
    assert [r["strike"] for r in d["rows"]] == sorted(r["strike"] for r in d["rows"])
    c = next(r for r in d["rows"] if r["strike"] == 74000.0 and r["type"] == "C")
    src = next(r for r in fx("deribit_BTC.json")["result"] if r["instrument_name"] == "BTC-25DEC26-74000-C")
    assert c["name"] == "BTC-25DEC26-74000-C" and c["iv"] == pytest.approx(src["mark_iv"] / 100, abs=1e-5)
    assert c["bid"] == pytest.approx(src["bid_price"] * src["underlying_price"], rel=1e-5)
    assert c["ask"] == pytest.approx(src["ask_price"] * src["underlying_price"], rel=1e-5)
    assert c["oi"] == src["open_interest"] and c["volume"] == src["volume"]
    # no expiry given = the default one; text of the JSON holds no NaN / Infinity
    r = api.get("/api/options/chain?id=BTC")
    assert r.json()["expiry"]["key"] == "2026-11-27" and "NaN" not in r.text and "Infinity" not in r.text


def test_chain_route_for_cboe_keeps_source_greeks_and_usd_prices(api, up):
    d = api.get("/api/options/chain?id=AAPL&expiry=2027-01-15").json()
    assert d["source"] == "cboe" and d["greeks"]["origin"] == "source" and d["contract_size"] == 100.0
    assert d["native"] == "USD" and "mark_native" not in d["rows"][0]
    c = next(r for r in d["rows"] if r["strike"] == 420.0 and r["type"] == "C")
    assert (c["bid"], c["ask"], c["mark"], c["iv"], c["delta"], c["gamma"], c["vega"], c["theta"], c["oi"], c["volume"],
            c["derived"]) == (0.41, 0.5, 0.455, 0.207, 0.0385, 0.0027, 0.1327, -0.0193, 13517.0, 253.0, False)
    no_iv = [r for r in d["rows"] if r["iv"] is None]
    assert no_iv and all(r["delta"] is None and r["gamma"] is None for r in no_iv)
    # CBOE's greeks are in the units the payload states: Black-76 (r = 0) on the same IV agrees roughly
    s = R.black76(d["fwd"], 420.0, d["expiry"]["dte"] / 365.0, 0.207, "C")
    assert s["vega"] == pytest.approx(c["vega"], rel=0.35) and s["theta"] == pytest.approx(c["theta"], rel=0.35)
    assert s["delta"] == pytest.approx(c["delta"], abs=0.02) and s["gamma"] == pytest.approx(c["gamma"], rel=0.35)


def test_cboe_freshness_follows_the_file_timestamp(api, up, clock):
    # AAPL: the file was written at 11:01:30 UTC and fetched at 13:23 — last session's quotes
    d = api.get("/api/options?id=AAPL").json()
    assert d["freshness"] == "CLOSED" and d["delay"] == "D15" and "2026-11-12 11:01Z" in d["note"]
    assert time.gmtime(d["as_of"])[:5] == (2026, 11, 12, 11, 1) and d["fetched_at"] == RECORDED_AT
    assert [e["key"] for e in d["expiries"]] == ["2026-11-20", "2027-01-15"]
    # SPX: written two seconds before the fetch — delayed quotes of a market that is trading
    d = api.get("/api/options?id=SPX").json()
    assert d["freshness"] == "D15" and "15 minutes" in d["note"]
    assert d["multi_root"] is True and d["spot_kind"] == "implied" and d["published_price"] == 7619.3799
    assert [e["key"] for e in d["expiries"]] == ["2026-11-20.SPX", "2026-11-20.SPXW"]
    assert d["expiries"][0]["dte"] < d["expiries"][1]["dte"]


def test_cboe_label_follows_whether_the_quotes_move(api, up, clock, monkeypatch):
    """Seen on 2026-11-12: before the open CBOE rewrote AAPL's file every few minutes (fresh timestamp,
    yesterday's quotes), and the NDX file all night, while SPX and VIX really traded in global hours."""
    state = {"session": "closed", "bump": 0.0, "ts": RECORDED_AT - 5}
    monkeypatch.setattr(session, "state", lambda cal, now=None: state["session"])

    def fetch(symbol, **k):
        up.calls["cboe"] += 1
        raw = fx(CBOE_FILE[symbol])
        raw["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(state["ts"]))    # freshly written
        for o in raw["data"]["options"]:
            if o["bid"]:
                o["bid"] = round(o["bid"] + state["bump"], 2)
        return raw
    monkeypatch.setattr(cboe_options, "fetch_chain", fetch)

    def label(id_):
        d = api.get("/api/options?id=" + id_).json()
        return d["freshness"], d["note"]
    # a stock before the open: the file is two seconds old, the session is closed
    assert label("AAPL")[0] == "CLOSED" and "stock options trade 09:30-16:00" in label("AAPL")[1]
    # an index at night, first look: CBOE says delayed quotes; nothing proves otherwise yet
    assert label("SPX")[0] == "D15" and "extended hours" in label("SPX")[1]
    # ... the next fetches bring the very same quotes: after ten minutes it is called what it is
    for _ in range(6):
        clock.tick(R.CBOE_TTL + 1)
        state["ts"] = clock.now - 5
        got = label("SPX")
    assert got[0] == "CLOSED" and "no bid or ask in CBOE's file has changed since 2026-11-12 13:23Z" in got[1]
    # quotes move again (global trading hours, or the open): D15 at the next fetch
    state["bump"] = 0.05
    clock.tick(R.CBOE_TTL + 1)
    state["ts"] = clock.now - 5
    assert label("SPX")[0] == "D15"
    # the stock: session open, but the delayed feed has not moved yet -> still the last session's
    state["session"] = "open"
    state["bump"] = 0.0
    clock.tick(R.CBOE_TTL_IDLE + 1)
    state["ts"] = clock.now - 5
    assert label("AAPL")[0] == "CLOSED" and "has changed since" in label("AAPL")[1]
    state["bump"] = 0.10                                      # 15 minutes after the bell the first quotes arrive
    clock.tick(R.CBOE_TTL + 1)
    state["ts"] = clock.now - 5
    got = label("AAPL")
    assert got[0] == "D15" and got[1].startswith("CBOE delayed quotes: 15 minutes") and "closed" not in got[1]


def test_chain_route_expiry_parameter(api, up):
    r = api.get("/api/options/chain?id=SPX&expiry=2026-11-20")
    assert r.status_code == 400 and "2026-11-20.SPX, 2026-11-20.SPXW" in r.json()["error"]["message"]
    assert api.get("/api/options/chain?id=SPX&expiry=2026-11-20.SPXW").json()["expiry"]["root"] == "SPXW"
    r = api.get("/api/options/chain?id=BTC&expiry=2031-01-01")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_source"
    assert "2026-11-13" in r.json()["error"]["message"]
    for bad in ("tomorrow", "2026-12-25%27%3B--", "2026-12-25.spxw", "20261225", "2026-12-25/../x"):
        r = api.get("/api/options/chain?id=BTC&expiry=" + bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", bad
    assert api.get("/api/options/chain?id=BTC&expiry=" + "9" * 40).status_code == 400
    assert up.calls == {"deribit": 1, "cboe": 1}, "a refused parameter costs no upstream call"


def test_no_source_and_unknown_ids(api, up):
    for path in ("/api/options", "/api/options/chain", "/api/vol_surface", "/api/gex"):
        r = api.get(path + "?id=CL")
        assert r.status_code == 404, path
        err = r.json()["error"]
        assert err["code"] == "no_source" and "NO LIVE SOURCE" in err["message"] and "BTC" in err["message"]
        assert api.get(path + "?id=EURUSD").status_code == 404 and api.get(path + "?id=US10Y").status_code == 404
        assert api.get(path + "?id=DOGE").status_code == 400
        assert api.get(path).status_code == 400
        assert api.get(path + "?underlying=BTC").status_code == 400      # the old parameter name is gone
    assert up.calls == {"deribit": 0, "cboe": 0}


def test_four_views_cost_one_upstream_call_per_ttl(api, up, clock):
    for path in ("/api/options", "/api/options/chain", "/api/vol_surface", "/api/gex", "/api/gex", "/api/options"):
        assert api.get(path + "?id=BTC").status_code == 200
    assert up.calls["deribit"] == 1
    clock.tick(R.DERIBIT_TTL - 1)
    api.get("/api/gex?id=BTC")
    assert up.calls["deribit"] == 1
    clock.tick(2)
    api.get("/api/gex?id=BTC")
    assert up.calls["deribit"] == 2
    # SOL shares nothing with BTC (other currency); each CBOE underlying is its own file
    api.get("/api/options?id=SOL")
    api.get("/api/options?id=AAPL")
    api.get("/api/gex?id=AAPL")
    assert up.calls == {"deribit": 3, "cboe": 1}


def test_cboe_files_are_cached_for_minutes(api, up, clock, monkeypatch):
    monkeypatch.setattr(session, "state", lambda cal, now=None: "open")
    assert api.get("/api/options?id=AAPL").json()["ttl"] == R.CBOE_TTL
    clock.tick(R.CBOE_TTL - 5)
    api.get("/api/options/chain?id=AAPL")
    assert up.calls["cboe"] == 1
    clock.tick(10)
    api.get("/api/options?id=AAPL")
    assert up.calls["cboe"] == 2
    # outside the session an equity file does not change: ten minutes
    monkeypatch.setattr(session, "state", lambda cal, now=None: "closed")
    clock.tick(R.CBOE_TTL + 60)
    assert api.get("/api/options?id=AAPL").json()["ttl"] == R.CBOE_TTL_IDLE, "the page is told the TTL in force"
    assert up.calls["cboe"] == 2
    clock.tick(R.CBOE_TTL_IDLE)
    api.get("/api/options?id=AAPL")
    assert up.calls["cboe"] == 3
    assert api.get("/api/options?id=SPX").json()["ttl"] == R.CBOE_TTL, "index options trade almost round the clock"
    assert R.CBOE_TTL >= 120 and R.CBOE_TTL_IDLE >= 600 and R.DERIBIT_TTL >= 10


def test_stale_answer_when_the_source_fails_and_an_error_when_there_is_nothing(api, up, clock):
    up.down.add("deribit")
    r = api.get("/api/options?id=BTC")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"
    assert r.json()["error"]["source"] == "deribit"
    up.down.clear()
    clock.tick(10)
    first = api.get("/api/gex?id=BTC").json()
    assert "stale" not in first
    up.down.add("deribit")
    clock.tick(R.DERIBIT_TTL + 1)
    for path in ("/api/gex", "/api/options", "/api/options/chain", "/api/vol_surface"):
        d = api.get(path + "?id=BTC").json()
        assert d["stale"] is True and d["error"]["source"] == "deribit" and "503" in d["error"]["message"], path
        assert d["as_of"] == first["as_of"], "the age shown is the age of the data, not of the failed retry"
    assert api.get("/api/gex?id=BTC").json()["total"] == first["total"]
    clock.tick(700)                                           # past the stale window: no old chain is served
    assert api.get("/api/options?id=BTC").status_code == 502


def test_an_expiry_that_passes_while_the_chain_is_cached_disappears(api, up, clock):
    clock.now = 1794556800.0 - 60                             # one minute before the 13 Nov 2026 08:00 UTC expiry
    assert [e["key"] for e in api.get("/api/options?id=BTC").json()["expiries"]][0] == "2026-11-13"
    up.down.add("deribit")
    clock.tick(65)                                            # the refresh fails: the old chain is served, minus what expired
    d = api.get("/api/options?id=BTC").json()
    assert [e["key"] for e in d["expiries"]] == ["2026-11-27", "2026-12-25"] and d["stale"] is True
    assert api.get("/api/options/chain?id=BTC&expiry=2026-11-13").status_code == 404


def test_vol_surface_route(api, up):
    d = api.get("/api/vol_surface?id=BTC").json()
    grid = d["moneyness"]
    assert grid == sorted(grid) and 1.0 in grid and grid[0] == 0.5 and grid[-1] >= 1.5 and len(grid) <= 37
    assert len(d["iv"]) == len(d["expiries"]) == 3 and all(len(row) == len(grid) for row in d["iv"])
    j = grid.index(1.0)
    for e, row in zip(d["expiries"], d["iv"]):
        assert row[j] == pytest.approx(e["atm_iv"], abs=0.004), "the m = 1 column is the ATM IV"
        assert 0.2 < e["atm_iv"] < 1.0 and e["p25"] > e["atm_iv"] > 0 and e["c25"] > 0
        for m, v in zip(grid, row):
            k = m * e["fwd"]
            assert (v is None) == (k < e["k_min"] or k > e["k_max"]), "null exactly where no strike brackets it"
            assert v is None or 0.1 < v < 3.0
    # one cell by hand: 25 Dec, m = 0.9 -> strike 0.9 x forward, between two listed strikes, OTM side = puts
    e, row = d["expiries"][2], d["iv"][2]
    k = 0.9 * e["fwd"]
    puts = sorted((r["strike"], r["iv"]) for r in api.get("/api/options/chain?id=BTC&expiry=2026-12-25").json()["rows"]
                  if r["type"] == "P")
    lo = max(p for p in puts if p[0] <= k)
    hi = min(p for p in puts if p[0] >= k)
    want = lo[1] + (hi[1] - lo[1]) * (k - lo[0]) / (hi[0] - lo[0])
    assert row[grid.index(0.9)] == pytest.approx(want, abs=2e-5)
    # the band follows the underlying's volatility: an index at 14 % is narrower than BTC at 40 %
    spx = api.get("/api/vol_surface?id=SPX").json()
    assert 0.75 <= spx["moneyness"][0] <= 0.8 and 1.2 <= spx["moneyness"][-1] <= 1.25 and 1.0 in spx["moneyness"]
    assert spx["moneyness"][-1] - spx["moneyness"][0] < grid[-1] - grid[0]
    assert "interpolat" in d["method"] or "linear" in d["method"]


def _hand_chain():
    """A chain small enough to work out on paper. Level 100, IV 20 % everywhere, expiries in a quarter
    of a year and in one year (vol = 0.2 x sqrt(T) = 0.10 and 0.20). Black-76 gamma = pdf(d1) / (F x vol):

      T = 0.25  K = 100  d1 = 0.05      pdf = 0.398444  gamma = 0.0398444
      T = 0.25  K =  90  d1 = 1.103605  pdf = 0.216988  gamma = 0.0216988
      T = 1.00  K = 100  d1 = 0.10      pdf = 0.396953  gamma = 0.0198476

    exposure = gamma x contracts x 100 x 100^2 x 1 % / 1e6 = gamma x contracts x 0.0001  ($mm per 1 %)."""
    exp1, exp2 = RECORDED_AT + 0.25 * 365 * 86400, RECORDED_AT + 365 * 86400

    def c(key, exp, k, cp, src_gamma, oi, iv=0.2):
        return {"name": f"T{key}{cp}{k}", "key": key, "date": key, "root": "T", "exp_ts": exp, "strike": float(k),
                "type": cp, "bid": 1.0, "ask": 1.2, "mark": 1.1, "iv": iv, "oi": float(oi), "volume": 0.0, "fwd": None,
                "last": None, "last_t": None, "delta": None if src_gamma is None else (0.5 if cp == "C" else -0.5),
                "gamma": src_gamma, "vega": 0.1, "theta": -0.01}
    return {"source": "cboe", "symbol": "TEST", "as_of": RECORDED_AT, "spot": 100.0, "spot_src": "test", "native": "USD",
            "contracts": [
                c("2027-02-11", exp1, 100, "C", 0.05, 10),      # +0.0398444 x 10 x 0.0001 = +0.00398444
                c("2027-02-11", exp1, 100, "P", 0.05, 0),       #  no open interest: nothing (it gives the parity forward 100)
                c("2027-02-11", exp1, 90, "P", 0.02, 20),       # -0.0216988 x 20 x 0.0001 = -0.00433976
                c("2027-11-12", exp2, 100, "C", 0.03, 5),       # +0.0198476 x  5 x 0.0001 = +0.00099238
                c("2027-11-12", exp2, 100, "P", 0.03, 15),      # -0.0198476 x 15 x 0.0001 = -0.00297714
                c("2027-11-12", exp2, 110, "C", None, 50, iv=None),   # open interest but no IV: cannot be used
            ]}


def test_gex_by_hand():
    ch = R.build_chain(_hand_chain(), fetched_at=RECORDED_AT)
    assert [s["fwd"] for s in ch["series"]] == [100.0, 100.0] and ch["spot"] == 100.0
    g = R._gex(ch)
    assert g["strikes"] == [90.0, 100.0]
    assert g["call"] == pytest.approx([0.0, 0.00398444 + 0.00099238], abs=2e-8)
    assert g["put"] == pytest.approx([-0.00433976, -0.00297714], abs=2e-8)
    assert g["call_oi"] == [0.0, 15.0] and g["put_oi"] == [20.0, 15.0]
    assert sum(g["call"]) + sum(g["put"]) == pytest.approx(-0.00234008, abs=5e-8)
    assert [(e["key"], round(e["call"], 6), round(e["put"], 6), round(e["net"], 6)) for e in g["by_expiry"]] == [
        ("2027-02-11", 0.003984, -0.00434, -0.000355), ("2027-11-12", 0.000992, -0.002977, -0.001985)]
    assert g["oi_total"] == 100.0 and g["oi_used"] == 50.0                 # the 50 contracts without an IV are not covered
    assert g["call_wall"] == 100.0 and g["put_wall"] == 90.0
    # cross-check with the gammas the "source" sent (0.05, 0.02, 0.03, 0.03) at the level 100:
    # +0.05 x 10 x 0.0001 - 0.02 x 20 x 0.0001 + 0.03 x 5 x 0.0001 - 0.03 x 15 x 0.0001 = -0.0020
    assert g["src_total"] == pytest.approx(-0.002, abs=1e-12)
    # the profile at today's level is the same sum, and it is what `_reprice` gets with black76 itself
    assert R._profile_at_one(g) == pytest.approx(-0.00234008, abs=5e-8)
    for m in (0.8, 0.93, 1.0, 1.1, 1.27):
        assert R._profile_at(_prof(ch), m) == pytest.approx(_reprice(ch, m), rel=1e-9, abs=1e-12), m


def _prof(ch):
    """The (a, b, c) lines of the profile, rebuilt from the chain exactly as _gex documents them."""
    out = []
    for s in ch["series"]:
        for r in s["rows"]:
            if not r.oi or r.gm is None:
                continue
            f = r.f or s["fwd"]
            vol = r.iv * math.sqrt(s["t"])
            out.append(((math.log(f / r.k) + 0.5 * vol * vol) / vol, 1.0 / vol,
                        (1 if r.cp == "C" else -1) * r.oi * ch["mult"] * f / vol * 1e-8 / math.sqrt(2 * math.pi)))
    return out


def test_gex_route_shape_and_totals(api, up, monkeypatch):
    hand = _hand_chain()
    monkeypatch.setattr(cboe_options, "parse_chain", lambda payload, symbol, **k: {**hand, "symbol": symbol})
    d = api.get("/api/gex?id=AAPL").json()
    assert d["unit"] == "$mm per 1% move" and d["strikes"] == [90.0, 100.0]
    assert d["gex"] == pytest.approx([-0.00433976, 0.00199968], abs=1e-7) and d["total"] == pytest.approx(-0.00234008, abs=1e-7)
    assert d["call_total"] == pytest.approx(0.00497682, abs=1e-7) and d["put_total"] == pytest.approx(-0.0073169, abs=1e-7)
    assert d["expiries_used"] == 2 and d["expiries_listed"] == 2 and d["oi_covered_pct"] == 50.0
    only_unusable = {**hand, "contracts": hand["contracts"][:3] + hand["contracts"][-1:]}      # 2nd expiry: OI without IV only
    monkeypatch.setattr(cboe_options, "parse_chain", lambda payload, symbol, **k: {**only_unusable, "symbol": symbol})
    cache.clear()
    d2 = api.get("/api/gex?id=AAPL").json()
    assert d2["expiries_used"] == 1 and d2["expiries_listed"] == 2, "an expiry whose open interest has no IV is listed, not used"
    assert d2["by_expiry"][1]["net"] == 0 and d2["by_expiry"][1]["oi"] == 50.0
    assert d["call_wall"] == {"strike": 100.0, "gex": pytest.approx(0.00497682, abs=1e-7)}
    assert d["put_wall"] == {"strike": 90.0, "gex": pytest.approx(-0.00433976, abs=1e-7)}
    assert [e["oi_pct"] for e in d["by_expiry"]] == [30.0, 70.0]
    assert "long calls" in d["convention"] and "assum" in d["convention"] and "1%" in d["formula"]
    assert d["gamma_origin"] == "derived" and d["source_gamma_total"] == pytest.approx(-0.002)
    assert len(d["profile"]["spots"]) == len(d["profile"]["gex"]) == len(d["profile"]["pct"]) == 61
    assert d["profile"]["spots"][30] == 100.0 and d["profile"]["pct"][30] == 0.0
    assert d["profile"]["at_spot"] == pytest.approx(d["total"], abs=1e-9)
    # more put than call open interest at every maturity: negative at every level, so there is no flip
    assert max(d["profile"]["gex"]) < 0 and d["flip"] is None and d["flip_pct"] is None
    assert "does not change sign" in d["flip_note"]


def test_gex_over_all_expiries_matches_an_independent_sum(api, up):
    """The old route used the four nearest expiries (about 11 % of BTC open interest) and one forward
    for all of them. Recompute from the raw fixture rows, each with its own forward."""
    d = api.get("/api/gex?id=BTC").json()
    rows = fx("deribit_BTC.json")["result"]
    as_of = d["as_of"]
    per_k, by_exp, oi_all = {}, {}, 0.0
    for r in rows:
        _, date, exp_ts, k, cp = deribit.parse_name(r["instrument_name"])
        oi, f, iv = r["open_interest"], r["underlying_price"], r["mark_iv"] / 100.0
        oi_all += oi
        t = (exp_ts - as_of) / (365 * 86400.0)
        d1 = (math.log(f / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
        gamma = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (f * iv * math.sqrt(t))
        g = (1 if cp == "C" else -1) * gamma * oi * f * f * 0.01 / 1e6
        per_k[k] = per_k.get(k, 0.0) + g
        by_exp[date] = by_exp.get(date, 0.0) + g
    ks = sorted(k for k in per_k if any(parse[3] == k and row["open_interest"] > 0 for row in rows
                                        for parse in [deribit.parse_name(row["instrument_name"])]))
    assert d["strikes"] == ks
    assert d["gex"] == pytest.approx([per_k[k] for k in ks], rel=1e-4, abs=1e-6)
    assert d["total"] == pytest.approx(sum(per_k.values()), rel=1e-4)
    assert {e["key"]: e["net"] for e in d["by_expiry"]} == pytest.approx(by_exp, rel=1e-4, abs=1e-6)
    assert d["expiries_used"] == 3 and d["expiries_listed"] == 3 and d["oi_covered_pct"] == 100.0
    assert d["oi_total"] == pytest.approx(oi_all) and d["oi_unit"] == "BTC"
    assert len(d["by_expiry"]) == 3 and sum(e["oi_pct"] for e in d["by_expiry"]) == pytest.approx(100.0, abs=0.01)
    assert d["call_total"] > 0 > d["put_total"] and d["total"] == pytest.approx(d["call_total"] + d["put_total"], rel=1e-4)
    # at today's level the re-priced profile is the same number (same model, same inputs)
    assert d["profile"]["at_spot"] == pytest.approx(d["total"], rel=1e-4)
    assert d["call_wall"]["gex"] == max(d["call_gex"]) and d["put_wall"]["gex"] == min(d["put_gex"])


def test_zero_gamma_level_is_where_the_repriced_total_changes_sign(api, up):
    d = api.get("/api/gex?id=BTC").json()
    assert d["flip"] is not None and 0.7 * d["spot"] < d["flip"] < 1.3 * d["spot"]
    assert d["flip_pct"] == pytest.approx((d["flip"] / d["spot"] - 1) * 100, abs=0.01)
    spots, prof = d["profile"]["spots"], d["profile"]["gex"]
    i = max(j for j, s in enumerate(spots) if s <= d["flip"])
    assert prof[i] * prof[i + 1] <= 0, "the flip lies between two grid points of opposite sign"
    # and re-pricing the cached chain exactly there gives (almost) nothing
    ch = cache.peek("deribit:options:BTC").value
    g = ch["_memo"]["gex"]
    rebuilt = R._gex(ch)
    assert rebuilt["flip_m"] == g["flip_m"]
    scale = max(abs(v) for v in prof)
    assert abs(R._profile_at([], 1.0)) == 0.0
    assert abs(_reprice(ch, g["flip_m"])) < 1e-3 * scale


def _reprice(ch, m):
    """Total exposure with every forward moved to m x its value — written out with black76 itself."""
    total = 0.0
    for s in ch["series"]:
        for r in s["rows"]:
            if not r.oi or r.iv is None:
                continue
            f = (r.f or s["fwd"]) * m
            g = R.black76(f, r.k, s["t"], r.iv, r.cp)
            total += (1 if r.cp == "C" else -1) * R.gex_usd_mm(g["gamma"], r.oi * ch["mult"], f)
    return total


def test_gex_without_a_sign_change_says_so():
    snap = _hand_chain()
    snap["contracts"] = [c for c in snap["contracts"] if c["type"] == "C" and c["iv"] is not None]
    g = R._gex(R.build_chain(snap, fetched_at=RECORDED_AT))
    assert g["flip_m"] is None and all(v >= 0 for v in g["profile"]) and g["put_wall"] is None


def test_cboe_gex_uses_black76_gammas_and_reports_the_sum_with_cboes_own(api, up):
    """CBOE's gammas have 4 decimals: fine for a $355 share (0.0214), one digit for an index at 7,575
    (0.0012). The exposure uses Black-76 on each row's own CBOE IV; CBOE's own sum is the cross-check."""
    d = api.get("/api/gex?id=AAPL").json()
    assert d["gamma_origin"] == "derived" and d["source_gamma_total"] is not None and "4 decimals" in d["source_gamma_note"]
    assert d["total"] == pytest.approx(d["source_gamma_total"], rel=0.10), "a share: both sums agree within a few per cent"
    assert d["profile"]["at_spot"] == pytest.approx(d["total"], rel=1e-4)
    assert 90.0 < d["oi_covered_pct"] <= 100.0 and d["contract_size"] == 100.0
    assert d["expiries_used"] == 2 and d["call_total"] > 0 > d["put_total"]
    by_hand = 0.0
    ch = cache.peek("cboe:options:AAPL").value
    for s in ch["series"]:
        for r in s["rows"]:
            if r.oi and r.iv is not None:
                g = R.black76(s["fwd"], r.k, s["t"], r.iv, r.cp)["gamma"]
                by_hand += (1 if r.cp == "C" else -1) * g * r.oi * 100 * s["fwd"] ** 2 * 0.01 / 1e6
    assert d["total"] == pytest.approx(by_hand, rel=1e-4)
    assert api.get("/api/gex?id=BTC").json()["source_gamma_total"] is None      # Deribit sends no gammas


def test_unquoted_placeholder_series_are_not_listed(api, up, monkeypatch):
    """Seen in CBOE's NVDA and TSLA files: a root "B1VDA" / "B1SLA" with no bid, ask, IV or open interest."""
    raw = fx("cboe_AAPL.json")
    ghost = {"option": "B1APL261120C00355000", "bid": 0.0, "ask": 0.0, "iv": 0.0, "open_interest": 0.0, "volume": 0.0,
             "delta": 0.0378, "gamma": -0.0002, "vega": -0.0002, "theta": 0.0145, "last_trade_price": 0.0,
             "last_trade_time": None}
    raw["data"]["options"] = [ghost, {**ghost, "option": "B1APL261120P00355000"}] + raw["data"]["options"]
    monkeypatch.setattr(cboe_options, "fetch_chain", lambda symbol, **k: raw)
    d = api.get("/api/options?id=AAPL").json()
    assert [e["key"] for e in d["expiries"]] == ["2026-11-20.AAPL", "2027-01-15"] and d["default_expiry"] == "2026-11-20.AAPL"
    assert d["skipped"]["unquoted_series"] == ["2026-11-20.B1APL"] and d["skipped"]["expired_contracts"] > 20


def test_default_expiry_prefers_the_root_with_open_interest(api, up):
    d = api.get("/api/options?id=SPX").json()
    oi = {e["key"]: e["oi"] for e in d["expiries"]}
    assert d["default_expiry"] == max(oi, key=oi.get)


def test_a_source_file_without_quotes_or_iv_is_reported_as_blank_not_as_zero(api, up, monkeypatch):
    """Seen live on 2026-11-12 14:42-14:46 UTC (the delayed image of the minutes before the US open): every
    bid, ask and iv in CBOE's SPX and AAPL files was 0 while open interest was still listed."""
    raw = fx("cboe_AAPL.json")
    for o in raw["data"]["options"]:
        o["bid"] = o["ask"] = o["iv"] = 0.0
        o["delta"] = o["gamma"] = o["vega"] = o["theta"] = 0.0
    monkeypatch.setattr(cboe_options, "fetch_chain", lambda symbol, **k: raw)
    d = api.get("/api/options?id=AAPL").json()
    assert d["blank"] is True and "no implied volatility" in d["note"] and "no bid or ask" in d["note"]
    assert all(e["atm_iv"] is None and e["oi"] > 0 for e in d["expiries"]) and d["spot"] == 355.57
    c = api.get("/api/options/chain?id=AAPL").json()
    assert c["blank"] is True and all(r["bid"] is None and r["iv"] is None and r["delta"] is None for r in c["rows"])
    assert any(r["oi"] > 0 for r in c["rows"]), "open interest is still shown"
    g = api.get("/api/gex?id=AAPL").json()
    assert g["blank"] is True and g["total"] is None and g["call_total"] is None and g["put_total"] is None
    assert g["strikes"] == [] and g["flip"] is None and g["oi_covered_pct"] == 0.0 and g["expiries_used"] == 0
    assert g["profile"]["spots"] == [] and g["profile"]["at_spot"] is None and g["source_gamma_total"] is None
    v = api.get("/api/vol_surface?id=AAPL").json()
    assert v["blank"] is True and v["moneyness"] == [] and all(row == [] for row in v["iv"])
    for path in ("/api/options", "/api/options/chain", "/api/gex", "/api/vol_surface"):
        r = api.get(path + "?id=AAPL")
        assert r.status_code == 200 and "NaN" not in r.text
    assert "blank" not in api.get("/api/options?id=BTC").json()


def test_sol_route_uses_usdc_rows(api, up):
    d = api.get("/api/options/chain?id=SOL&expiry=2026-12-25").json()
    assert d["source"] == "deribit" and d["native"] == "USDC" and "USDC" in d["price_note"]
    assert all(r["name"].startswith("SOL_USDC-25DEC26-") for r in d["rows"]) and len(d["rows"]) > 60
    assert 70 < d["spot"] < 95 and d["oi_unit"] == "SOL"
    r = next(r for r in d["rows"] if r["name"] == "SOL_USDC-25DEC26-92-P")
    assert r["mark"] == pytest.approx(12.7921, abs=1e-4) and r["mark_native"] == r["mark"] and r["oi"] == 3890.0
    g = api.get("/api/gex?id=SOL").json()
    assert g["oi_covered_pct"] == 100.0 and g["oi_unit"] == "SOL"


def test_empty_or_fully_expired_source_is_an_upstream_error(api, up, monkeypatch):
    monkeypatch.setattr(deribit, "fetch_summary", lambda currency, **k: {"jsonrpc": "2.0", "result": [], "usOut": 1})
    r = api.get("/api/options?id=ETH")
    assert r.status_code == 502 and "no ETH options" in r.json()["error"]["message"]


def test_the_route_file_is_no_longer_the_quarantined_legacy_one():
    import app as gateway
    assert gateway.LEGACY_MARK not in (R.__doc__ or "") and "options" not in gateway.QUARANTINED
    paths = {r.path for r in R.router.routes}
    assert paths == {"/api/options", "/api/options/chain", "/api/vol_surface", "/api/gex"}
    import inspect
    src = inspect.getsource(R) + inspect.getsource(deribit) + inspect.getsource(cboe_options)
    assert "urllib" not in src and "import random" not in src and "import lse" not in src and "lse." not in src
    for fn in (R.options, R.options_chain, R.vol_surface, R.gex):
        assert not inspect.iscoroutinefunction(fn), "routes are plain def (threadpool)"
