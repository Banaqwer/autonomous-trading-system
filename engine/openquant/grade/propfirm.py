"""Prop-firm evaluation simulator.

Prices a strategy against a funded-account program via Monte Carlo. Given the
strategy's daily P&L distribution (scaled to the account), it simulates many
evaluation attempts under the firm's rules — profit target, daily-loss limit,
trailing/static max drawdown, evaluation fee, reset policy — then the funded
phase and payout cycle. Returns pass odds, expected cost to get funded,
probability of a first payout, and expected net cash.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class PropFirmSpec:
    account_size: float = 100_000.0
    profit_target: float = 8_000.0      # eval phase target ($)
    daily_loss_limit: float = 4_000.0   # max loss in a single day ($)
    max_drawdown: float = 6_000.0       # trailing max drawdown ($)
    trailing: bool = True               # trailing vs static drawdown
    eval_fee: float = 150.0             # per attempt
    payout_split: float = 0.8           # trader's share of funded profits
    payout_threshold: float = 4_000.0   # profit needed before first payout
    max_eval_days: int = 30
    funded_horizon_days: int = 60
    max_attempts: int = 5


@dataclass
class PropFirmResult:
    pass_rate: float                 # P(pass evaluation in <= max_attempts)
    avg_attempts_to_pass: float
    expected_cost_to_funded: float
    prob_first_payout: float
    expected_net_cash: float
    go: bool
    suggested_contracts: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _daily_returns_usd(result, spec: PropFirmSpec) -> np.ndarray:
    """Scale the backtest's per-bar equity returns to the prop account size."""
    r = result.returns.dropna().to_numpy(dtype=float)
    if len(r) < 20:
        # fall back to per-trade pnls spread over days
        r = np.array(result.trade_log.trade_pnls(), dtype=float) / max(result.initial_equity, 1)
    return r * spec.account_size


def _run_phase(daily: np.ndarray, target: float, dll: float, max_dd: float,
               trailing: bool, days: int, rng) -> tuple[bool, bool, float]:
    """Simulate one phase. Returns (passed, busted, ending_pnl)."""
    sample = rng.choice(daily, size=days, replace=True)
    equity = 0.0
    peak = 0.0
    for d in sample:
        # daily loss limit on the single-day move
        if d < -dll:
            return (False, True, equity + d)
        equity += d
        peak = max(peak, equity)
        floor = (peak - max_dd) if trailing else (-max_dd)
        if equity <= floor:
            return (False, True, equity)
        if equity >= target:
            return (True, False, equity)
    return (False, False, equity)


def simulate_prop_firm(result, spec: PropFirmSpec | None = None,
                       n_sims: int = 20_000, seed: int = 3) -> PropFirmResult:
    spec = spec or PropFirmSpec()
    rng = np.random.default_rng(seed)
    daily = _daily_returns_usd(result, spec)
    if len(daily) == 0 or np.allclose(daily, 0):
        return PropFirmResult(0, 0, 0, 0, 0, False, 0, ["No usable daily P&L to simulate."])

    passed_flags = np.zeros(n_sims, dtype=bool)
    attempts_used = np.zeros(n_sims, dtype=float)
    cost = np.zeros(n_sims, dtype=float)
    payout = np.zeros(n_sims, dtype=bool)
    net_cash = np.zeros(n_sims, dtype=float)

    for s in range(n_sims):
        funded = False
        attempts = 0
        spent = 0.0
        while attempts < spec.max_attempts and not funded:
            attempts += 1
            spent += spec.eval_fee
            ok, _, _ = _run_phase(daily, spec.profit_target, spec.daily_loss_limit,
                                  spec.max_drawdown, spec.trailing, spec.max_eval_days, rng)
            if ok:
                funded = True
        attempts_used[s] = attempts
        cost[s] = spent
        passed_flags[s] = funded
        if funded:
            # funded phase: same risk rules, measure profit over the horizon
            _, busted, ending = _run_phase(daily, target=1e18, dll=spec.daily_loss_limit,
                                            max_dd=spec.max_drawdown, trailing=spec.trailing,
                                            days=spec.funded_horizon_days, rng=rng)
            trader_profit = max(ending, 0.0) * spec.payout_split
            got_payout = ending >= spec.payout_threshold
            payout[s] = got_payout
            net_cash[s] = (trader_profit if got_payout else 0.0) - spent
        else:
            net_cash[s] = -spent

    pass_rate = float(passed_flags.mean())
    avg_attempts = float(attempts_used[passed_flags].mean()) if passed_flags.any() else float(spec.max_attempts)
    exp_cost = float(cost[passed_flags].mean()) if passed_flags.any() else float(spec.eval_fee * spec.max_attempts)
    prob_payout = float(payout.mean())
    exp_net = float(net_cash.mean())

    # position sizing suggestion: scale so daily-loss breach stays rare (~1%)
    day_std = daily.std() or 1.0
    safe_scale = (spec.daily_loss_limit / (2.33 * day_std))  # 99% one-sided
    suggested = round(max(0.1, min(safe_scale, 5.0)), 2)

    notes = []
    go = exp_net > 0 and pass_rate > 0.25
    notes.append(f"Edge EV is {'positive' if exp_net > 0 else 'negative'} "
                 f"(${exp_net:,.0f} expected net per attempt-cycle).")
    if pass_rate < 0.25:
        notes.append(f"Low pass rate ({pass_rate:.0%}); evaluation fees likely exceed funded profit.")
    if safe_scale < 1:
        notes.append("Daily volatility is high vs the daily-loss limit; size down.")

    return PropFirmResult(pass_rate, avg_attempts, exp_cost, prob_payout, exp_net, go, suggested, notes)
