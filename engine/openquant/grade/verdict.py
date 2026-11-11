"""The 'Verdict' grader.

Produces a letter bucket + a 0-100 composite built from four sub-scores:
  edge           statistical significance of the per-trade mean (bootstrap p)
                 combined with annualized Sharpe
  robustness     how spread the P&L is across trades and regimes (not one lucky
                 trade / one regime)
  risk           drawdown and tail behaviour (smaller is better)
  sample_adequacy   number of independent trades (more -> more trustworthy)

Nothing here claims to know a strategy is good; it quantifies how much the
evidence supports the backtest, which is exactly what a research reviewer does.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Verdict:
    bucket: str
    score: int
    sub_scores: dict
    stats: dict
    p_value: float
    confidence_intervals: dict
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "bucket": self.bucket, "score": self.score,
            "sub_scores": self.sub_scores, "p_value": round(self.p_value, 4),
            "confidence_intervals": self.confidence_intervals,
            "stats": self.stats, "notes": self.notes,
        }


def _bootstrap_mean(pnls: np.ndarray, n: int, rng) -> np.ndarray:
    return rng.choice(pnls, size=(n, len(pnls)), replace=True).mean(axis=1)


def _logistic(x: float, k: float, x0: float) -> float:
    return 100.0 / (1.0 + np.exp(-k * (x - x0)))


def grade_verdict(
    result,
    regime_attr: dict | None = None,
    n_boot: int = 5_000,
    seed: int = 11,
) -> Verdict:
    stats = result.stats()
    pnls = np.array(result.trade_log.trade_pnls(), dtype=float)
    notes: list[str] = []
    rng = np.random.default_rng(seed)

    n = len(pnls)
    if n == 0:
        return Verdict("F", 0, {"edge": 0, "robustness": 0, "risk": 0, "sample_adequacy": 0},
                       stats, 1.0, {}, ["No trades were taken."])

    # --- bootstrap p-value: P(mean <= 0) ---
    boot = _bootstrap_mean(pnls, n_boot, rng)
    p_value = float((boot <= 0).mean())
    ci = {
        "68": [round(float(np.percentile(boot, 16)), 2), round(float(np.percentile(boot, 84)), 2)],
        "90": [round(float(np.percentile(boot, 5)), 2), round(float(np.percentile(boot, 95)), 2)],
        "95": [round(float(np.percentile(boot, 2.5)), 2), round(float(np.percentile(boot, 97.5)), 2)],
    }

    # --- edge: significance + risk-adjusted return ---
    sharpe = stats["sharpe"]
    sig_score = (1.0 - min(p_value, 1.0)) * 100.0
    sharpe_score = _logistic(sharpe, k=3.0, x0=0.7)
    edge = 0.6 * sig_score + 0.4 * sharpe_score

    # --- robustness: P&L concentration (Gini-like) + regime spread ---
    wins = pnls[pnls > 0]
    top_share = (wins.max() / wins.sum()) if wins.sum() > 0 else 1.0
    concentration_score = (1.0 - min(top_share, 1.0)) * 100.0
    regime_score = 50.0
    if regime_attr:
        share = abs(regime_attr.get("top_regime_pnl_share", 1.0))
        nreg = regime_attr.get("num_regimes_traded", 1)
        regime_score = (1.0 - min(share, 1.0)) * 60.0 + min(nreg, 4) / 4.0 * 40.0
        if share > 0.8:
            notes.append(f"P&L concentrated in one regime ({regime_attr.get('top_regime')}, "
                         f"{share:.0%} of net).")
    robustness = 0.5 * concentration_score + 0.5 * regime_score

    # --- risk: drawdown + profit factor floor ---
    max_dd = abs(stats["max_drawdown_pct"])
    dd_score = max(0.0, 100.0 - max_dd * 2.5)   # 0% dd -> 100, 40% dd -> 0
    pf = stats["profit_factor"] or 0.0
    pf_score = _logistic(pf, k=1.2, x0=1.3)
    risk = 0.7 * dd_score + 0.3 * pf_score
    if max_dd > 30:
        notes.append(f"Deep max drawdown ({max_dd:.1f}%).")

    # --- sample adequacy: trade count ---
    sample = _logistic(n, k=0.06, x0=40)   # ~50 at 40 trades, saturates by ~120
    if n < 30:
        notes.append(f"Only {n} trades — statistically thin; treat results as provisional.")

    subs = {
        "edge": int(round(edge)),
        "robustness": int(round(robustness)),
        "risk": int(round(risk)),
        "sample_adequacy": int(round(sample)),
    }
    # weighted composite: edge and sample adequacy gate the headline
    composite = (0.35 * subs["edge"] + 0.20 * subs["robustness"]
                 + 0.20 * subs["risk"] + 0.25 * subs["sample_adequacy"])
    # significance gate: an insignificant edge caps the grade
    if p_value > 0.10:
        composite = min(composite, 55)
        notes.append(f"Edge not significant at 90% (bootstrap p={p_value:.2f}).")
    score = int(round(composite))

    bucket = ("A" if score >= 85 else "B" if score >= 72 else "C" if score >= 58
              else "D" if score >= 45 else "F")
    return Verdict(bucket, score, subs, stats, p_value, ci, notes)
