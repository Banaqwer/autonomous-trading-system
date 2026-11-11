"""Canonical trade-log format.

This 15-column schema is the contract between a backtest and the grading engine
(the same bridge QuantPad uses between a Project backtest and a Quant Copilot
session). Each closed round-trip produces two rows (entry + exit) sharing a
trade number, with P&L and excursion (MFE/MAE) recorded on the exit row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

CANONICAL_COLUMNS = [
    "Trade #", "Type", "Date and time", "Signal",
    "Price USD", "Size (qty)", "Size (value)",
    "Net P&L USD", "Net P&L %",
    "Favorable excursion USD", "Favorable excursion %",
    "Adverse excursion USD", "Adverse excursion %",
    "Cumulative P&L USD", "Cumulative P&L %",
]


@dataclass
class TradeLog:
    rows: list[dict] = field(default_factory=list)
    _cum: float = 0.0
    _equity0: float = 0.0

    def set_initial_equity(self, equity: float) -> None:
        self._equity0 = equity

    def entry(self, n: int, ts: datetime, side: str, signal: str, price: float, qty: float) -> None:
        self.rows.append({
            "Trade #": n,
            "Type": f"Entry {side}",
            "Date and time": ts,
            "Signal": signal,
            "Price USD": round(price, 4),
            "Size (qty)": qty,
            "Size (value)": round(price * qty, 2),
            "Net P&L USD": "", "Net P&L %": "",
            "Favorable excursion USD": "", "Favorable excursion %": "",
            "Adverse excursion USD": "", "Adverse excursion %": "",
            "Cumulative P&L USD": "", "Cumulative P&L %": "",
        })

    def exit(self, n: int, ts: datetime, side: str, signal: str, price: float, qty: float,
             pnl: float, mfe: float, mae: float, entry_value: float) -> None:
        self._cum += pnl
        base = self._equity0 or entry_value or 1.0
        self.rows.append({
            "Trade #": n,
            "Type": f"Exit {side}",
            "Date and time": ts,
            "Signal": signal,
            "Price USD": round(price, 4),
            "Size (qty)": qty,
            "Size (value)": round(price * qty, 2),
            "Net P&L USD": round(pnl, 2),
            "Net P&L %": round(100.0 * pnl / entry_value, 4) if entry_value else 0.0,
            "Favorable excursion USD": round(mfe, 2),
            "Favorable excursion %": round(100.0 * mfe / entry_value, 4) if entry_value else 0.0,
            "Adverse excursion USD": round(mae, 2),
            "Adverse excursion %": round(100.0 * mae / entry_value, 4) if entry_value else 0.0,
            "Cumulative P&L USD": round(self._cum, 2),
            "Cumulative P&L %": round(100.0 * self._cum / base, 4),
        })

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=CANONICAL_COLUMNS)

    def to_csv(self, path: str) -> str:
        self.to_frame().to_csv(path, index=False)
        return path

    def trade_pnls(self) -> list[float]:
        return [r["Net P&L USD"] for r in self.rows if str(r["Type"]).startswith("Exit")]
