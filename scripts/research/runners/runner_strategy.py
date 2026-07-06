"""Runner-momentum strategy logic — pure functions over a 1-min session frame.

No I/O. A "session" is a single trading day of 1-min OHLCV bars with an ET
DatetimeIndex (columns: open, high, low, close, volume).

The strategy: on an intraday small-cap runner, wait for a tight consolidation
("coil") riding the high-of-day, then enter when a bar closes above the coil
high on a volume surge. Stop = coil low; scale half at +1R to breakeven; trail
the remainder with a chandelier; flatten by EOD.

See docs/superpowers/specs/2026-07-01-runner-momentum-backtest-design.md
"""
from __future__ import annotations

import datetime

import pandas as pd

from scripts.research.swing.strategies import stop_fill_price


def track_hod(bars: pd.DataFrame) -> pd.Series:
    """Running high-of-day: cumulative max of the high column."""
    return bars["high"].cummax()


def _coil_window(bars: pd.DataFrame, i: int, n_bars: int) -> pd.DataFrame:
    """The `n_bars` bars immediately preceding candidate-breakout bar `i`."""
    return bars.iloc[i - n_bars:i]


def detect_coil(bars: pd.DataFrame, i: int, n_bars: int, max_range_pct: float) -> bool:
    """True if the `n_bars` before bar `i` are a tight consolidation at the HOD.

    Tight: (coil_high - coil_low) / coil_low <= max_range_pct.
    At HOD: the coil's high is >= the highest high of all bars before the coil
    window (the pause is riding new highs, not a mid-pullback chop).
    """
    if i < n_bars:
        return False
    window = _coil_window(bars, i, n_bars)
    coil_high = float(window["high"].max())
    coil_low = float(window["low"].min())
    if coil_low <= 0:
        return False
    range_pct = (coil_high - coil_low) / coil_low
    tight = range_pct <= max_range_pct

    prior = bars.iloc[:i - n_bars]
    at_hod = prior.empty or coil_high >= float(prior["high"].max())
    return bool(tight and at_hod)


def entry_signal(
    bars: pd.DataFrame,
    i: int,
    n_bars: int,
    max_range_pct: float,
    vol_mult: float,
) -> bool:
    """True if bar `i` breaks the coil high on a volume surge.

    Requires: a valid coil in the preceding `n_bars`, bar `i` CLOSES above the
    coil high, and bar `i` volume >= vol_mult x the coil's average volume.
    """
    if not detect_coil(bars, i, n_bars, max_range_pct):
        return False
    window = _coil_window(bars, i, n_bars)
    coil_high = float(window["high"].max())
    coil_avg_vol = float(window["volume"].mean())

    bar = bars.iloc[i]
    breaks = float(bar["close"]) > coil_high
    vol_surge = float(bar["volume"]) >= vol_mult * coil_avg_vol
    return bool(breaks and vol_surge)


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    """Wilder-style ATR via a simple rolling mean of true range.

    NaN until `period` bars are available; callers must treat NaN as
    "chandelier not yet active".
    """
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def simulate_trade(
    bars: pd.DataFrame,
    entry_i: int,
    coil_low: float,
    *,
    atr_mult: float = 2.5,
    atr_period: int = 14,
    slip_bps: float = 30.0,
    eod_flat_time: datetime.time = datetime.time(15, 55),
) -> dict:
    """Simulate one long runner trade from entry to exit.

    Entry is the CLOSE of bar `entry_i` (the bar that broke the coil). Planned
    risk R = raw_entry - coil_low. Management, in priority order per bar after
    entry:
      1. Scale 50% at +1R (limit at entry+R); move stop up to breakeven.
      2. Chandelier trail the remainder: stop = max(stop, HH_since_entry -
         atr_mult*ATR), once ATR is available.
      3. Stop hit (bar low <= stop) -> exit remainder at stop_fill_price(stop,
         bar_open) so gap-downs fill at the open, not the stop.
      4. EOD flatten (bar time >= eod_flat_time) -> exit remainder at close.
    If bars run out with an open position, flatten at the last close.

    Slippage (slip_bps per side) raises the buy fill and lowers every sell fill.
    All prices returned are post-slippage fills; r_multiple/return_pct are
    measured against those fills and the planned R.
    """
    slip = slip_bps / 10_000.0
    raw_entry = float(bars["close"].iloc[entry_i])
    stop = float(coil_low)
    r_dollars = raw_entry - stop
    target1 = raw_entry + r_dollars
    entry_fill = raw_entry * (1.0 + slip)

    atr = _atr(bars, atr_period)

    exits: list[dict] = []          # {qty, price (fill), reason, time}
    remaining = 1.0
    scaled = False
    hh = raw_entry                  # highest high since entry (for chandelier)

    def _sell(qty: float, raw_price: float, reason: str, ts) -> None:
        exits.append({
            "qty": qty,
            "price": raw_price * (1.0 - slip),
            "reason": reason,
            "time": ts,
        })

    for j in range(entry_i + 1, len(bars)):
        bar = bars.iloc[j]
        ts = bars.index[j]
        high = float(bar["high"])
        low = float(bar["low"])
        bar_open = float(bar["open"])
        hh = max(hh, high)

        # 1. scale half at +1R
        if not scaled and high >= target1:
            _sell(0.5, target1, "scale1", ts)
            remaining -= 0.5
            scaled = True
            stop = max(stop, raw_entry)   # breakeven floor

        # 2. chandelier trail (only once ATR is defined)
        atr_j = atr.iloc[j]
        if pd.notna(atr_j):
            stop = max(stop, hh - atr_mult * float(atr_j))

        # 3. stop hit
        if low <= stop:
            _sell(remaining, stop_fill_price(stop, bar_open), "stop", ts)
            remaining = 0.0
            break

        # 4. EOD flatten
        if ts.time() >= eod_flat_time:
            _sell(remaining, float(bar["close"]), "eod", ts)
            remaining = 0.0
            break

    # ran out of bars with an open position -> flatten at last close
    if remaining > 0.0:
        last = bars.iloc[-1]
        _sell(remaining, float(last["close"]), "end", bars.index[-1])
        remaining = 0.0

    exit_avg = sum(e["qty"] * e["price"] for e in exits)
    return {
        "entry": entry_fill,
        "stop": stop,
        "r_dollars": r_dollars,
        "exits": exits,
        "exit_avg": exit_avg,
        "r_multiple": (exit_avg - entry_fill) / r_dollars if r_dollars else 0.0,
        "return_pct": exit_avg / entry_fill - 1.0,
        "reason": exits[-1]["reason"] if exits else None,
    }
