"""Bias-controlled intraday exit simulation and trade construction."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Trade:
    symbol: str
    date: str
    setup: str
    entry: float
    stop: float
    exit: float
    exit_reason: str
    r_multiple: float
    bars_held: int


def make_trade(symbol, date, setup, entry, stop, exit_price, reason, bars_held,
               slip_bps=15.0):
    """Apply slippage to entry (buy higher) and exit (sell lower) and compute R
    against the *planned* risk (entry - stop)."""
    slip = slip_bps / 10_000.0
    entry_fill = entry * (1 + slip)
    exit_fill = exit_price * (1 - slip)
    risk = entry - stop
    r = (exit_fill - entry_fill) / risk if risk > 0 else 0.0
    return Trade(symbol, date, setup, round(entry_fill, 4), round(stop, 4),
                 round(exit_fill, 4), reason, r, bars_held)


def simulate_exit(bars_after, entry_price, initial_stop, k=3.0):
    """Walk bars after entry; return (exit_price, reason, bars_held).

    Long-only. Each bar: ratchet a chandelier floor = highest_high - k*atr (never
    below initial_stop, never decreasing). Gap-through (open <= stop) fills at the
    bar open; intrabar (low <= stop) fills at the stop; otherwise force-flat at the
    last bar's close.
    """
    stop = initial_stop
    trailing = False  # True once chandelier ratchets above initial_stop
    highest_high = entry_price
    held = 0
    n = len(bars_after)
    for i in range(n):
        row = bars_after.iloc[i]
        held = i + 1
        if row["open"] <= stop:
            return float(row["open"]), "gap_stop", held
        if row["low"] <= stop:
            reason = "trail" if trailing else "stop"
            return float(stop), reason, held
        highest_high = max(highest_high, float(row["high"]))
        chandelier = highest_high - k * float(row["atr"])
        new_stop = max(stop, chandelier)
        if new_stop > initial_stop:
            trailing = True
        stop = new_stop
        if i == n - 1:
            return float(row["close"]), "eod", held
    return float(entry_price), "eod", held
