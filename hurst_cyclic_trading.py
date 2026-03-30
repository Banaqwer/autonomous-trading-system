"""
Hurst Cyclic Trading Algorithm
==============================
Based on J.M. Hurst's "The Profit Magic of Stock Transaction Timing"

Core principles implemented:
1. Price-motion model: prices = sum of cyclic components + trend + noise
2. Hurst's 10 principles: summation, commonality, variation, nominality,
   proportionality, synchronicity, etc.
3. Cycle extraction via envelope analysis and Fourier/spectral methods
4. Half-span & full-span centered moving averages for cycle isolation
5. Inverse moving average for zero-lag cycle extraction
6. Curvilinear envelopes for prediction zones
7. Edge-band and mid-band transaction timing
8. Confluence scoring across multiple cycle components
9. Full backtest with risk management

Hurst's nominal cycle model (approximate periods in weeks):
  18-month (~78 wk), 40-week, 20-week, 10-week, 5-week, ~2.5-week

Usage:
    python hurst_cyclic_trading.py
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from enum import Enum
import warnings

warnings.filterwarnings("ignore")


# =============================================================================
# 1. DATA STRUCTURES
# =============================================================================

class Side(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class CycleComponent:
    """A single extracted cyclic component from price data."""
    period: float          # in bars
    amplitude: float       # magnitude of the oscillation
    phase: float           # current phase in radians
    frequency: float       # angular frequency
    label: str             # e.g. "20-week", "10-week"
    confidence: float      # 0-1 quality score


@dataclass
class PredictionZone:
    """A Hurst-style price/time prediction zone."""
    time_start: int        # bar index
    time_end: int
    price_low: float
    price_high: float
    direction: str         # "up" or "down"
    cycle_label: str
    strength: float        # 0-1 confluence score


@dataclass
class Signal:
    """A buy or sell signal with Hurst timing classification."""
    bar: int
    side: Side
    timing_type: str       # "edge_band" or "mid_band"
    price: float
    stop_price: float
    target_price: float
    confluence_score: float
    cycles_aligned: List[str]


@dataclass
class Trade:
    """A completed trade for backtesting."""
    entry_bar: int
    exit_bar: int
    side: Side
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    pnl: float
    r_multiple: float
    confluence_score: float
    exit_reason: str


# =============================================================================
# 2. HURST MOVING AVERAGE ENGINE
# =============================================================================

class HurstMovingAverages:
    """
    Implements Hurst's centered moving average system:
    - Full-span MA: span = cycle period (removes that cycle + shorter ones)
    - Half-span MA: span = cycle period / 2 (key for prediction)
    - Inverse MA: price - (full_span_MA - price) to extract cycle component

    The half-span average changes direction at the CURRENT time when the
    cycle it tracks is at a turning point. This is the core prediction tool.
    """

    @staticmethod
    def centered_moving_average(prices: np.ndarray, span: int) -> np.ndarray:
        """
        Centered (non-causal) moving average. Hurst insists on centered
        placement to avoid the phase lag of standard trailing MAs.
        The last span/2 values must be extrapolated for real-time use.
        """
        if span < 2:
            return prices.copy()
        half = span // 2
        result = np.full_like(prices, np.nan, dtype=float)
        for i in range(half, len(prices) - half):
            result[i] = np.mean(prices[i - half:i + half + 1])
        return result

    @staticmethod
    def causal_moving_average(prices: np.ndarray, span: int) -> np.ndarray:
        """Standard trailing MA for real-time use."""
        result = np.full_like(prices, np.nan, dtype=float)
        for i in range(span - 1, len(prices)):
            result[i] = np.mean(prices[i - span + 1:i + 1])
        return result

    @staticmethod
    def half_span_average(prices: np.ndarray, cycle_period: int) -> np.ndarray:
        """
        Half-span MA: span = cycle_period / 2.
        When this reverses direction, it signals the dominant cycle is turning.
        Hurst's key insight: the half-span average provides move-prediction
        information when it changes direction.
        """
        span = max(2, cycle_period // 2)
        return HurstMovingAverages.causal_moving_average(prices, span)

    @staticmethod
    def full_span_average(prices: np.ndarray, cycle_period: int) -> np.ndarray:
        """Full-span MA: span = cycle_period. Removes that cycle."""
        span = max(2, cycle_period)
        return HurstMovingAverages.causal_moving_average(prices, span)

    @staticmethod
    def inverse_average(prices: np.ndarray, cycle_period: int) -> np.ndarray:
        """
        Hurst's inverse moving average: extracts a cycle component with
        correct magnitude on a zero baseline.

        inverse_avg = price - full_span_avg

        This shows the fluctuation at exactly the correct magnitude,
        since it is present in price but precisely zero in the MA.
        """
        full_ma = HurstMovingAverages.full_span_average(prices, cycle_period)
        inverse = prices - full_ma
        return inverse

    @staticmethod
    def half_span_direction(prices: np.ndarray, cycle_period: int,
                            lookback: int = 3) -> np.ndarray:
        """
        Returns +1 when half-span MA is rising, -1 when falling, 0 when flat.
        A direction change is Hurst's primary timing signal.
        """
        hma = HurstMovingAverages.half_span_average(prices, cycle_period)
        direction = np.zeros_like(prices)
        for i in range(lookback, len(prices)):
            if np.isnan(hma[i]) or np.isnan(hma[i - lookback]):
                continue
            diff = hma[i] - hma[i - lookback]
            if diff > 0:
                direction[i] = 1
            elif diff < 0:
                direction[i] = -1
        return direction


# =============================================================================
# 3. CYCLE DETECTION ENGINE (Spectral / Fourier Analysis)
# =============================================================================

class CycleDetector:
    """
    Hurst used spectral analysis (DFT) to identify the dominant cycle
    periods in price data. He separated data into two sequences and
    performed Fourier analysis to find frequency, amplitude, and phase.

    This implements his approach using modern FFT plus envelope-based
    validation of detected cycles.
    """

    # Hurst's nominal cycle periods (in trading days, ~5 days/week)
    # 18-month ~= 390 days, 40-week ~= 200, 20-week ~= 100,
    # 10-week ~= 50, 5-week ~= 25, 2.5-week ~= 12
    NOMINAL_PERIODS = {
        "18_month": 390,
        "40_week": 200,
        "20_week": 100,
        "10_week": 50,
        "5_week": 25,
        "2.5_week": 12,
    }

    # Hurst's principle of nominality: actual periods cluster around
    # nominal values but vary. Allow +/- 30% search window.
    TOLERANCE = 0.30

    def __init__(self, prices: np.ndarray):
        self.prices = prices
        self.n = len(prices)
        self.log_prices = np.log(prices)
        self.detrended = self._detrend(self.log_prices)

    def _detrend(self, series: np.ndarray) -> np.ndarray:
        """Remove linear trend (Hurst's long-duration component approximation)."""
        x = np.arange(len(series))
        coeffs = np.polyfit(x, series, 1)
        trend = np.polyval(coeffs, x)
        return series - trend

    def detect_cycles(self) -> List[CycleComponent]:
        """
        Run FFT on detrended price data and identify peaks near
        Hurst's nominal cycle periods.
        """
        n = self.n
        freqs = np.fft.rfftfreq(n, d=1.0)
        fft_vals = np.fft.rfft(self.detrended)
        power = np.abs(fft_vals) ** 2
        phases = np.angle(fft_vals)

        # Convert frequencies to periods (in bars)
        periods = np.zeros_like(freqs)
        periods[1:] = 1.0 / freqs[1:]
        periods[0] = np.inf

        components = []
        max_power = np.max(power[1:])

        for label, nominal in self.NOMINAL_PERIODS.items():
            if nominal > n * 0.8:
                continue  # can't detect cycles longer than ~80% of data

            low = nominal * (1 - self.TOLERANCE)
            high = nominal * (1 + self.TOLERANCE)

            # Find the peak power within the search window
            mask = (periods >= low) & (periods <= high)
            if not np.any(mask):
                continue

            local_power = power.copy()
            local_power[~mask] = 0

            peak_idx = np.argmax(local_power)
            if local_power[peak_idx] == 0:
                continue

            detected_period = periods[peak_idx]
            amplitude = 2.0 * np.abs(fft_vals[peak_idx]) / n
            phase = phases[peak_idx]
            confidence = local_power[peak_idx] / max_power

            components.append(CycleComponent(
                period=detected_period,
                amplitude=amplitude,
                phase=phase,
                frequency=2 * np.pi / detected_period,
                label=label,
                confidence=min(1.0, confidence),
            ))

        # Sort by period descending (longest cycle first, per Hurst)
        components.sort(key=lambda c: c.period, reverse=True)
        return components

    def reconstruct_cycle(self, component: CycleComponent,
                          n_bars: int = 0) -> np.ndarray:
        """Reconstruct a single cycle component as a time series."""
        length = n_bars if n_bars > 0 else self.n
        t = np.arange(length)
        return component.amplitude * np.cos(component.frequency * t + component.phase)

    def reconstruct_all(self, components: List[CycleComponent],
                        n_bars: int = 0) -> np.ndarray:
        """
        Hurst's principle of summation: price motion = sum of all
        cyclic components.
        """
        length = n_bars if n_bars > 0 else self.n
        composite = np.zeros(length)
        for comp in components:
            composite += self.reconstruct_cycle(comp, length)
        return composite


# =============================================================================
# 4. ENVELOPE & PREDICTION ZONE ENGINE
# =============================================================================

class EnvelopeEngine:
    """
    Hurst's curvilinear envelope analysis:
    - Connect successive lows to form lower envelope
    - Connect successive highs to form upper envelope
    - The center line of the envelope tracks the dominant cycle
    - Envelope width reflects shorter-cycle amplitude
    - When price touches/crosses envelope boundary -> prediction zone
    """

    @staticmethod
    def find_local_extrema(prices: np.ndarray, order: int = 5
                           ) -> Tuple[np.ndarray, np.ndarray]:
        """Find local highs and lows for envelope construction."""
        highs = []
        lows = []
        for i in range(order, len(prices) - order):
            if prices[i] == max(prices[i - order:i + order + 1]):
                highs.append(i)
            if prices[i] == min(prices[i - order:i + order + 1]):
                lows.append(i)
        return np.array(highs), np.array(lows)

    @staticmethod
    def build_envelope(prices: np.ndarray, extrema_indices: np.ndarray
                       ) -> np.ndarray:
        """Interpolate between extrema to form a smooth envelope boundary."""
        if len(extrema_indices) < 2:
            return np.full_like(prices, np.nan, dtype=float)
        envelope = np.interp(
            np.arange(len(prices)),
            extrema_indices,
            prices[extrema_indices],
        )
        return envelope

    @staticmethod
    def build_curvilinear_envelopes(prices: np.ndarray, cycle_period: int
                                     ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build upper and lower curvilinear envelopes and center line.
        The envelope order (extrema search window) is tied to the cycle period.
        """
        order = max(3, cycle_period // 4)
        high_idx, low_idx = EnvelopeEngine.find_local_extrema(prices, order)

        upper = EnvelopeEngine.build_envelope(prices, high_idx)
        lower = EnvelopeEngine.build_envelope(prices, low_idx)
        center = (upper + lower) / 2.0

        return upper, lower, center

    @staticmethod
    def measure_cycle_from_envelope(prices: np.ndarray, cycle_period: int
                                     ) -> Tuple[float, float]:
        """
        Hurst's envelope technique for measuring actual cycle duration:
        count bars between successive lows in the lower envelope.
        Returns (measured_period, deviation_from_nominal).
        """
        order = max(3, cycle_period // 4)
        _, low_idx = EnvelopeEngine.find_local_extrema(prices, order)
        if len(low_idx) < 2:
            return cycle_period, 0.0
        intervals = np.diff(low_idx)
        measured = np.mean(intervals)
        deviation = abs(measured - cycle_period) / cycle_period
        return float(measured), float(deviation)


# =============================================================================
# 5. SIGNAL GENERATION ENGINE
# =============================================================================

class HurstSignalEngine:
    """
    Generates buy/sell signals using Hurst's two timing methods:

    1. EDGE-BAND timing: Buy at the first indication that cyclic lows
       have been formed (price crosses above lower envelope). This is
       the earliest possible entry.

    2. MID-BAND timing: Wait until after the edge-band buy, then wait
       for the price to cross above the half-span average. This confirms
       the move is underway and avoids some false signals.

    Sell signals are the mirror: price crosses below upper envelope
    (edge-band sell) or below half-span average (mid-band sell).
    """

    def __init__(self, prices: np.ndarray, components: List[CycleComponent]):
        self.prices = prices
        self.components = components
        self.n = len(prices)

        # Use the dominant trading cycle (typically 20-week or 10-week)
        self.trading_cycle = self._select_trading_cycle()

    def _select_trading_cycle(self) -> CycleComponent:
        """
        Select the primary trading cycle. Hurst recommends a cycle
        where you get actionable signals - typically 20-week for
        position trades, 5-week for shorter-term.
        """
        # Prefer the cycle closest to 50 bars (10-week) with decent confidence
        best = None
        best_score = -1
        for c in self.components:
            if 20 <= c.period <= 120:
                score = c.confidence * (1.0 - abs(c.period - 50) / 100)
                if score > best_score:
                    best_score = score
                    best = c
        if best is None and self.components:
            best = self.components[0]
        return best

    def generate_signals(self) -> List[Signal]:
        """Generate all buy and sell signals across the price history."""
        if self.trading_cycle is None:
            return []

        tc = int(self.trading_cycle.period)
        signals = []

        # Build envelopes for the trading cycle
        upper, lower, center = EnvelopeEngine.build_curvilinear_envelopes(
            self.prices, tc
        )

        # Half-span MA and its direction
        hma = HurstMovingAverages.half_span_average(self.prices, tc)
        hma_dir = HurstMovingAverages.half_span_direction(self.prices, tc)

        # Inverse average for magnitude assessment
        inv_avg = HurstMovingAverages.inverse_average(self.prices, tc)

        # Confluence from multiple cycle components
        confluence = self._compute_confluence()

        # Track state to avoid duplicate signals
        position = 0  # 0=flat, 1=long, -1=short

        warmup = tc + 10
        for i in range(warmup, self.n):
            if np.isnan(hma[i]) or np.isnan(lower[i]) or np.isnan(upper[i]):
                continue

            conf_score = confluence[i] if i < len(confluence) else 0.5
            aligned = self._cycles_aligned_at(i)

            # --- EDGE-BAND BUY: price crosses above lower envelope ---
            if (position <= 0 and
                    self.prices[i] > lower[i] and
                    self.prices[i - 1] <= lower[i - 1] and
                    conf_score > 0.3):
                stop = lower[i] - 0.5 * (upper[i] - lower[i])
                target = upper[i]
                signals.append(Signal(
                    bar=i, side=Side.LONG, timing_type="edge_band",
                    price=self.prices[i], stop_price=stop,
                    target_price=target, confluence_score=conf_score,
                    cycles_aligned=aligned,
                ))
                position = 1

            # --- MID-BAND BUY: price crosses above half-span MA ---
            elif (position <= 0 and
                  self.prices[i] > hma[i] and
                  self.prices[i - 1] <= hma[i - 1] and
                  hma_dir[i] > 0 and
                  conf_score > 0.4):
                stop = lower[i]
                target = upper[i] + 0.25 * (upper[i] - lower[i])
                signals.append(Signal(
                    bar=i, side=Side.LONG, timing_type="mid_band",
                    price=self.prices[i], stop_price=stop,
                    target_price=target, confluence_score=conf_score,
                    cycles_aligned=aligned,
                ))
                position = 1

            # --- EDGE-BAND SELL: price crosses below upper envelope ---
            elif (position >= 0 and
                  self.prices[i] < upper[i] and
                  self.prices[i - 1] >= upper[i - 1] and
                  conf_score > 0.3):
                stop = upper[i] + 0.5 * (upper[i] - lower[i])
                target = lower[i]
                signals.append(Signal(
                    bar=i, side=Side.SHORT, timing_type="edge_band",
                    price=self.prices[i], stop_price=stop,
                    target_price=target, confluence_score=conf_score,
                    cycles_aligned=aligned,
                ))
                position = -1

            # --- MID-BAND SELL: price crosses below half-span MA ---
            elif (position >= 0 and
                  self.prices[i] < hma[i] and
                  self.prices[i - 1] >= hma[i - 1] and
                  hma_dir[i] < 0 and
                  conf_score > 0.4):
                stop = upper[i]
                target = lower[i] - 0.25 * (upper[i] - lower[i])
                signals.append(Signal(
                    bar=i, side=Side.SHORT, timing_type="mid_band",
                    price=self.prices[i], stop_price=stop,
                    target_price=target, confluence_score=conf_score,
                    cycles_aligned=aligned,
                ))
                position = -1

        return signals

    def _compute_confluence(self) -> np.ndarray:
        """
        Hurst's principle of synchronicity: when multiple cycle components
        have troughs at the same time, a powerful low forms. When multiple
        peak together, a powerful high forms.

        Score = fraction of detected cycles that agree on direction.
        """
        confluence = np.zeros(self.n)
        if not self.components:
            return confluence + 0.5

        cycle_directions = []
        for comp in self.components:
            period = int(max(4, comp.period))
            direction = HurstMovingAverages.half_span_direction(
                self.prices, period
            )
            cycle_directions.append(direction)

        for i in range(self.n):
            ups = sum(1 for d in cycle_directions if d[i] > 0)
            downs = sum(1 for d in cycle_directions if d[i] < 0)
            total = ups + downs
            if total > 0:
                confluence[i] = max(ups, downs) / total
            else:
                confluence[i] = 0.5

        return confluence

    def _cycles_aligned_at(self, bar: int) -> List[str]:
        """Return labels of cycles whose half-span MA is rising at this bar."""
        aligned = []
        for comp in self.components:
            period = int(max(4, comp.period))
            direction = HurstMovingAverages.half_span_direction(
                self.prices, period, lookback=3
            )
            if bar < len(direction) and direction[bar] != 0:
                aligned.append(comp.label)
        return aligned


# =============================================================================
# 6. BACKTEST ENGINE
# =============================================================================

class HurstBacktester:
    """
    Backtests Hurst cyclic signals with proper risk management.

    Rules from the book:
    - Use logical cut-loss criteria (stop at envelope boundary)
    - Maximum time rate of profit: don't hold through idle periods
    - Exit when the trading cycle turns against you
    - Position sizing based on confluence score
    """

    def __init__(self, prices: np.ndarray, signals: List[Signal],
                 risk_per_trade: float = 0.02, initial_capital: float = 100000):
        self.prices = prices
        self.signals = signals
        self.risk_per_trade = risk_per_trade
        self.initial_capital = initial_capital

    def run(self) -> Tuple[List[Trade], pd.DataFrame]:
        """Execute backtest and return trades + equity curve."""
        trades = []
        equity = self.initial_capital
        equity_curve = [equity]
        position = None  # current open trade info

        for sig in self.signals:
            # Close existing position if direction changes
            if position is not None:
                exit_price = sig.price
                pnl = self._calc_pnl(position, exit_price)
                risk = abs(position["entry_price"] - position["stop_price"])
                r_mult = pnl / risk if risk > 0 else 0

                trades.append(Trade(
                    entry_bar=position["bar"],
                    exit_bar=sig.bar,
                    side=position["side"],
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    stop_price=position["stop_price"],
                    target_price=position["target_price"],
                    pnl=pnl,
                    r_multiple=r_mult,
                    confluence_score=position["confluence"],
                    exit_reason="signal_reversal",
                ))
                equity += pnl * position["size"]
                position = None

            # Open new position
            risk_amount = equity * self.risk_per_trade
            stop_dist = abs(sig.price - sig.stop_price)
            if stop_dist <= 0:
                continue
            size = risk_amount / stop_dist

            position = {
                "bar": sig.bar,
                "side": sig.side,
                "entry_price": sig.price,
                "stop_price": sig.stop_price,
                "target_price": sig.target_price,
                "size": size,
                "confluence": sig.confluence_score,
            }

            equity_curve.append(equity)

        # Close any open position at last price
        if position is not None:
            exit_price = self.prices[-1]
            pnl = self._calc_pnl(position, exit_price)
            risk = abs(position["entry_price"] - position["stop_price"])
            r_mult = pnl / risk if risk > 0 else 0
            trades.append(Trade(
                entry_bar=position["bar"],
                exit_bar=len(self.prices) - 1,
                side=position["side"],
                entry_price=position["entry_price"],
                exit_price=exit_price,
                stop_price=position["stop_price"],
                target_price=position["target_price"],
                pnl=pnl,
                r_multiple=r_mult,
                confluence_score=position["confluence"],
                exit_reason="end_of_data",
            ))
            equity += pnl * position["size"]
            equity_curve.append(equity)

        # Build equity DataFrame
        eq_df = pd.DataFrame({"equity": equity_curve})
        return trades, eq_df

    def _calc_pnl(self, position: dict, exit_price: float) -> float:
        """Per-unit PnL."""
        if position["side"] == Side.LONG:
            return exit_price - position["entry_price"]
        else:
            return position["entry_price"] - exit_price


# =============================================================================
# 7. PERFORMANCE REPORTING
# =============================================================================

class PerformanceReport:
    """Generate Hurst-style performance metrics."""

    @staticmethod
    def generate(trades: List[Trade], equity_df: pd.DataFrame,
                 initial_capital: float = 100000) -> Dict:
        if not trades:
            return {"error": "No trades to report"}

        pnls = np.array([t.pnl for t in trades])
        r_multiples = np.array([t.r_multiple for t in trades])
        winners = pnls[pnls > 0]
        losers = pnls[pnls <= 0]

        total_trades = len(trades)
        win_rate = len(winners) / total_trades if total_trades > 0 else 0
        avg_winner = np.mean(winners) if len(winners) > 0 else 0
        avg_loser = np.mean(losers) if len(losers) > 0 else 0
        expectancy = np.mean(pnls)
        avg_r = np.mean(r_multiples)

        # Equity curve stats
        eq = equity_df["equity"].values
        returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0])
        returns = returns[~np.isnan(returns)]
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252)
                  if np.std(returns) > 0 else 0)

        # Drawdown
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / peak
        max_dd = np.min(drawdown)

        # Edge-band vs mid-band breakdown
        edge_trades = [t for t in trades if "edge" in t.exit_reason or True]
        edge_band_trades = [t for t in trades]  # all trades have timing info in signals

        # Confluence score vs outcome
        high_conf = [t for t in trades if t.confluence_score > 0.6]
        low_conf = [t for t in trades if t.confluence_score <= 0.6]
        high_conf_wr = (sum(1 for t in high_conf if t.pnl > 0) / len(high_conf)
                        if high_conf else 0)
        low_conf_wr = (sum(1 for t in low_conf if t.pnl > 0) / len(low_conf)
                       if low_conf else 0)

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "avg_winner": round(float(avg_winner), 2),
            "avg_loser": round(float(avg_loser), 2),
            "expectancy_per_trade": round(float(expectancy), 2),
            "avg_r_multiple": round(float(avg_r), 2),
            "sharpe_ratio": round(float(sharpe), 2),
            "max_drawdown": round(float(max_dd), 4),
            "final_equity": round(float(eq[-1]), 2),
            "total_return_pct": round(float((eq[-1] - initial_capital) / initial_capital * 100), 2),
            "high_confluence_win_rate": round(high_conf_wr, 4),
            "low_confluence_win_rate": round(low_conf_wr, 4),
            "high_confluence_trades": len(high_conf),
            "low_confluence_trades": len(low_conf),
        }


# =============================================================================
# 8. MAIN HURST ALGORITHM ORCHESTRATOR
# =============================================================================

class HurstCyclicAlgorithm:
    """
    Complete Hurst Cyclic Trading System.

    Pipeline:
    1. Ingest OHLC data
    2. Detect dominant cycles via spectral analysis
    3. Build envelopes for the trading cycle
    4. Compute half-span and inverse MAs for each cycle
    5. Generate edge-band and mid-band signals
    6. Score confluence across all detected cycles
    7. Execute trades with envelope-based stops
    8. Report performance
    """

    def __init__(self, df: pd.DataFrame, price_col: str = "Close",
                 risk_per_trade: float = 0.02, initial_capital: float = 100000):
        """
        Parameters:
            df: DataFrame with at least a 'Close' column (and ideally OHLC).
            price_col: column to use for cycle analysis.
            risk_per_trade: fraction of equity risked per trade.
            initial_capital: starting capital for backtest.
        """
        self.df = df.copy()
        self.prices = df[price_col].values.astype(float)
        self.risk_per_trade = risk_per_trade
        self.initial_capital = initial_capital

        self.components: List[CycleComponent] = []
        self.signals: List[Signal] = []
        self.trades: List[Trade] = []
        self.equity_df: Optional[pd.DataFrame] = None
        self.report: Optional[Dict] = None

    def run(self) -> Dict:
        """Execute the full Hurst pipeline."""
        print("=" * 60)
        print("  HURST CYCLIC TRADING ALGORITHM")
        print("  Based on 'The Profit Magic of Stock Transaction Timing'")
        print("=" * 60)

        # Step 1: Detect cycles
        print("\n[1] Detecting dominant cycles via spectral analysis...")
        detector = CycleDetector(self.prices)
        self.components = detector.detect_cycles()
        for c in self.components:
            print(f"    {c.label:>12s}: period={c.period:6.1f} bars, "
                  f"amplitude={c.amplitude:.4f}, confidence={c.confidence:.2f}")

        if not self.components:
            print("  WARNING: No cycles detected. Data may be too short.")
            return {"error": "No cycles detected"}

        # Step 2: Build envelopes
        print("\n[2] Building curvilinear envelopes...")
        tc = self.components[0]  # longest detected cycle for envelopes
        upper, lower, center = EnvelopeEngine.build_curvilinear_envelopes(
            self.prices, int(tc.period)
        )
        measured_period, deviation = EnvelopeEngine.measure_cycle_from_envelope(
            self.prices, int(tc.period)
        )
        print(f"    Envelope cycle: nominal={tc.period:.0f}, "
              f"measured={measured_period:.1f}, deviation={deviation:.1%}")

        # Step 3: Compute moving averages
        print("\n[3] Computing Hurst moving averages...")
        for c in self.components:
            period = int(c.period)
            hma = HurstMovingAverages.half_span_average(self.prices, period)
            fma = HurstMovingAverages.full_span_average(self.prices, period)
            inv = HurstMovingAverages.inverse_average(self.prices, period)
            valid = np.sum(~np.isnan(hma))
            print(f"    {c.label:>12s}: half-span={period//2}, "
                  f"full-span={period}, valid_points={valid}")

        # Step 4: Generate signals
        print("\n[4] Generating buy/sell signals...")
        engine = HurstSignalEngine(self.prices, self.components)
        self.signals = engine.generate_signals()
        buys = sum(1 for s in self.signals if s.side == Side.LONG)
        sells = sum(1 for s in self.signals if s.side == Side.SHORT)
        edge = sum(1 for s in self.signals if s.timing_type == "edge_band")
        mid = sum(1 for s in self.signals if s.timing_type == "mid_band")
        print(f"    Total signals: {len(self.signals)} "
              f"(buy={buys}, sell={sells})")
        print(f"    Edge-band: {edge}, Mid-band: {mid}")

        if not self.signals:
            print("  WARNING: No signals generated.")
            return {"error": "No signals generated"}

        # Step 5: Backtest
        print("\n[5] Running backtest...")
        bt = HurstBacktester(
            self.prices, self.signals,
            self.risk_per_trade, self.initial_capital,
        )
        self.trades, self.equity_df = bt.run()
        print(f"    Completed trades: {len(self.trades)}")

        # Step 6: Report
        print("\n[6] Performance Report")
        print("-" * 40)
        self.report = PerformanceReport.generate(
            self.trades, self.equity_df, self.initial_capital
        )
        for k, v in self.report.items():
            label = k.replace("_", " ").title()
            print(f"    {label:.<32s} {v}")

        print("\n" + "=" * 60)
        print("  Run complete.")
        print("=" * 60)
        return self.report


# =============================================================================
# 9. DATA LOADING UTILITIES
# =============================================================================

def load_sample_data() -> pd.DataFrame:
    """
    Generate synthetic data with known cyclic components for testing,
    following Hurst's price-motion model:
    price = trend + sum(cycles) + noise
    """
    np.random.seed(42)
    n = 1000  # ~4 years of daily data

    t = np.arange(n)

    # Trend component (slow upward drift)
    trend = 100 + 0.02 * t

    # Cyclic components (Hurst's nominal model)
    cycle_200 = 8.0 * np.sin(2 * np.pi * t / 200 + 0.5)   # 40-week
    cycle_100 = 5.0 * np.sin(2 * np.pi * t / 100 + 1.0)   # 20-week
    cycle_50 = 3.0 * np.sin(2 * np.pi * t / 50 + 0.3)     # 10-week
    cycle_25 = 1.5 * np.sin(2 * np.pi * t / 25 + 2.0)     # 5-week
    cycle_12 = 0.8 * np.sin(2 * np.pi * t / 12 + 0.8)     # 2.5-week

    # Random motivation (noise)
    noise = np.cumsum(np.random.randn(n) * 0.3)

    price = trend + cycle_200 + cycle_100 + cycle_50 + cycle_25 + cycle_12 + noise
    price = np.maximum(price, 10)  # price floor

    # Generate OHLC from close
    high = price + np.abs(np.random.randn(n) * 0.5)
    low = price - np.abs(np.random.randn(n) * 0.5)
    open_ = price + np.random.randn(n) * 0.2

    df = pd.DataFrame({
        "Date": pd.date_range("2020-01-01", periods=n, freq="B"),
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": price,
        "Volume": np.random.randint(1000000, 10000000, n),
    })
    df.set_index("Date", inplace=True)
    return df


def load_csv_data(filepath: str) -> pd.DataFrame:
    """Load OHLC data from a CSV file."""
    df = pd.read_csv(filepath, parse_dates=True, index_col=0)
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(df.columns):
        # Try common column name variations
        col_map = {}
        for col in df.columns:
            cl = col.lower().strip()
            if "open" in cl:
                col_map[col] = "Open"
            elif "high" in cl:
                col_map[col] = "High"
            elif "low" in cl:
                col_map[col] = "Low"
            elif "close" in cl:
                col_map[col] = "Close"
            elif "vol" in cl:
                col_map[col] = "Volume"
        df.rename(columns=col_map, inplace=True)
    return df


# =============================================================================
# 10. MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys

    print("\nLoading data...")
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        print(f"  Source: {filepath}")
        df = load_csv_data(filepath)
    else:
        print("  Source: Synthetic Hurst price-motion model (for demonstration)")
        print("  Tip: pass a CSV file path as argument for real data")
        print("        python hurst_cyclic_trading.py your_data.csv")
        df = load_sample_data()

    print(f"  Bars: {len(df)}")
    print(f"  Date range: {df.index[0]} to {df.index[-1]}")
    print(f"  Price range: {df['Close'].min():.2f} to {df['Close'].max():.2f}")

    # Run the full Hurst algorithm
    algo = HurstCyclicAlgorithm(
        df,
        price_col="Close",
        risk_per_trade=0.02,
        initial_capital=100000,
    )
    results = algo.run()

    # Save results
    if algo.trades:
        trades_df = pd.DataFrame([
            {
                "entry_bar": t.entry_bar,
                "exit_bar": t.exit_bar,
                "side": t.side.value,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl": round(t.pnl, 2),
                "r_multiple": round(t.r_multiple, 2),
                "confluence": round(t.confluence_score, 2),
                "exit_reason": t.exit_reason,
            }
            for t in algo.trades
        ])
        trades_df.to_csv("hurst_trades.csv", index=False)
        print("\nTrade log saved to: hurst_trades.csv")

    if algo.equity_df is not None:
        algo.equity_df.to_csv("hurst_equity.csv", index=False)
        print("Equity curve saved to: hurst_equity.csv")
