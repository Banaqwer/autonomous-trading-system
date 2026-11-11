"""End-to-end demo: the full OpenQuant research pipeline on real data,
mirroring the 'canonical trade log -> Quant Copilot verdict' workflow."""
from openquant.data import Schema, get_data_client
from openquant.backtest import Backtester, MACrossover
from openquant.grade import (attribute_pnl_by_regime, grade_verdict,
                             regime_aware_mc, simulate_prop_firm, PropFirmSpec)

print("1. Pulling data (free Yahoo adapter; swap OPENQUANT_DATA=databento for institutional)…")
bars = get_data_client().get_bars("QQQ", Schema.OHLCV_1D)
print(f"   {len(bars)} daily bars for QQQ, {bars.index[0].date()} -> {bars.index[-1].date()}")

print("2. Backtesting a 30/100 MA crossover in a cloud-style sandbox…")
res = Backtester(initial_equity=100_000).run(bars, MACrossover(30, 100))
print("   stats:", res.stats())

print("3. Writing canonical 15-column trade log…")
path = res.trade_log.to_csv("/tmp/qqq_trades.csv")
print(f"   -> {path}  ({len(res.trade_log.trade_pnls())} closed trades)")

print("4. Regime attribution…")
reg = attribute_pnl_by_regime(res.trade_log, bars)
print("   top regime:", reg["top_regime"], "share:", reg["top_regime_pnl_share"])

print("5. Verdict grade…")
v = grade_verdict(res, regime_attr=reg)
print(f"   VERDICT {v.bucket} ({v.score}/100)  subs={v.sub_scores}  p={v.p_value:.3f}")
for n in v.notes:
    print("   -", n)

print("6. Regime-aware Monte Carlo (10k paths)…")
mc = regime_aware_mc(res.trade_log.trade_pnls(), regimes=None)
print("   prob_profit:", round(mc.prob_profit, 3), "median_pnl:", round(mc.median_pnl, 0),
      "worst_dd%:", round(mc.worst_max_drawdown, 1))

print("7. Prop-firm desk (20k sims)…")
pf = simulate_prop_firm(res, PropFirmSpec())
print("   pass_rate:", round(pf.pass_rate, 3), "exp_net_cash:", round(pf.expected_net_cash, 0),
      "go:", pf.go)
