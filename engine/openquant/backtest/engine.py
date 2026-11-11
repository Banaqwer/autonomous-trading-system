"""Event-driven, next-bar-open backtester.

Signals computed on bar i are executed at bar i+1's open (no look-ahead).
Applies commission + slippage, tracks per-trade MFE/MAE, and emits a canonical
trade log plus a per-bar mark-to-market equity curve. Long/flat/short supported.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy import Strategy
from .tradelog import TradeLog


@dataclass
class Fill:
    ts: pd.Timestamp
    price: float
    qty: float
    side: str


@dataclass
class Position:
    side: int = 0            # -1 short, 0 flat, +1 long
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: pd.Timestamp | None = None
    entry_value: float = 0.0
    entry_sig: str = "signal"
    mfe: float = 0.0         # max favorable excursion ($, >= 0)
    mae: float = 0.0         # max adverse excursion ($, <= 0)


@dataclass
class BacktestResult:
    equity: pd.Series
    trade_log: TradeLog
    returns: pd.Series
    symbol: str
    initial_equity: float
    params: dict = field(default_factory=dict)

    def stats(self) -> dict:
        eq = self.equity
        r = self.returns.dropna()
        pnls = np.array(self.trade_log.trade_pnls(), dtype=float)
        total = float(eq.iloc[-1] - self.initial_equity) if len(eq) else 0.0
        peak = eq.cummax()
        dd = (eq - peak) / peak
        max_dd = float(dd.min()) if len(dd) else 0.0
        ann = 252
        sharpe = float(np.sqrt(ann) * r.mean() / r.std()) if r.std() > 0 else 0.0
        downside = r[r < 0].std()
        sortino = float(np.sqrt(ann) * r.mean() / downside) if downside and downside > 0 else 0.0
        wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
        win_rate = float(len(wins) / len(pnls)) if len(pnls) else 0.0
        pf = float(wins.sum() / -losses.sum()) if losses.sum() != 0 else float("inf")
        expectancy = float(pnls.mean()) if len(pnls) else 0.0
        cagr = 0.0
        if len(eq) > 1 and eq.iloc[0] > 0:
            years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
            cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)
        return {
            "net_pnl_usd": round(total, 2),
            "return_pct": round(100 * total / self.initial_equity, 2),
            "cagr_pct": round(100 * cagr, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "max_drawdown_pct": round(100 * max_dd, 2),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 3) if np.isfinite(pf) else None,
            "expectancy_usd": round(expectancy, 2),
            "num_trades": int(len(pnls)),
        }


class Backtester:
    def __init__(
        self,
        initial_equity: float = 100_000.0,
        commission_per_share: float = 0.005,
        slippage_bps: float = 1.0,
        risk_fraction: float = 0.99,
    ):
        self.initial_equity = initial_equity
        self.commission = commission_per_share
        self.slippage_bps = slippage_bps
        self.risk_fraction = risk_fraction

    def _cost(self, price: float, qty: float) -> float:
        slip = price * (self.slippage_bps / 1e4)
        return self.commission * abs(qty) + slip * abs(qty)

    def run(self, bars: pd.DataFrame, strategy: Strategy) -> BacktestResult:
        bars = bars.sort_index()
        target = strategy.signals(bars).reindex(bars.index).fillna(0.0).to_numpy(dtype=float)
        opens = bars["open"].to_numpy(dtype=float)
        highs = bars["high"].to_numpy(dtype=float)
        lows = bars["low"].to_numpy(dtype=float)
        closes = bars["close"].to_numpy(dtype=float)
        idx = bars.index
        sym = str(bars["symbol"].iloc[0]) if "symbol" in bars.columns else "?"

        cash = self.initial_equity
        pos = Position()
        tl = TradeLog(); tl.set_initial_equity(self.initial_equity)
        equity = np.empty(len(bars), dtype=float)
        trade_no = 0

        def market_equity(i: int) -> float:
            # mark the open position to this bar's close
            if pos.side > 0:
                return cash + pos.qty * closes[i]
            if pos.side < 0:
                return cash - pos.qty * closes[i]
            return cash

        for i in range(len(bars)):
            # 1) update MFE/MAE on the open position using this bar's range
            if pos.side != 0:
                if pos.side > 0:
                    fav = (highs[i] - pos.entry_price) * pos.qty
                    adv = (lows[i] - pos.entry_price) * pos.qty
                else:
                    fav = (pos.entry_price - lows[i]) * pos.qty
                    adv = (pos.entry_price - highs[i]) * pos.qty
                pos.mfe = max(pos.mfe, fav)
                pos.mae = min(pos.mae, adv)

            # 2) act at this bar's open on the prior bar's target (no look-ahead)
            if i > 0 and int(target[i - 1]) != pos.side:
                want = int(target[i - 1])
                # close current position first
                if pos.side != 0:
                    px_exit = opens[i] * (1 - pos.side * self.slippage_bps / 1e4)
                    exit_cost = self._cost(px_exit, pos.qty)
                    if pos.side > 0:
                        cash += pos.qty * px_exit - exit_cost
                        gross = (px_exit - pos.entry_price) * pos.qty
                    else:
                        cash -= pos.qty * px_exit + exit_cost
                        gross = (pos.entry_price - px_exit) * pos.qty
                    entry_cost = self._cost(pos.entry_price, pos.qty)
                    pnl = gross - exit_cost - entry_cost
                    tl.exit(trade_no, idx[i], "long" if pos.side > 0 else "short",
                            pos.entry_sig, px_exit, pos.qty, pnl, pos.mfe, pos.mae, pos.entry_value)
                    pos = Position()
                # open new position
                if want != 0:
                    px_in = opens[i] * (1 + want * self.slippage_bps / 1e4)
                    qty = float(np.floor((cash * self.risk_fraction) / px_in))
                    if qty >= 1:
                        trade_no += 1
                        entry_cost = self._cost(px_in, qty)
                        if want > 0:
                            cash -= qty * px_in + entry_cost
                        else:
                            cash += qty * px_in - entry_cost
                        pos = Position(side=want, qty=qty, entry_price=px_in, entry_ts=idx[i],
                                       entry_value=px_in * qty, entry_sig=strategy.name)
                        tl.entry(trade_no, idx[i], "long" if want > 0 else "short",
                                 strategy.name, px_in, qty)

            equity[i] = market_equity(i)

        eq = pd.Series(equity, index=idx, name="equity")
        rets = eq.pct_change().rename("returns")
        params = {k: v for k, v in getattr(strategy, "__dict__", {}).items() if not k.startswith("_")}
        return BacktestResult(eq, tl, rets, sym, self.initial_equity, params=params)
