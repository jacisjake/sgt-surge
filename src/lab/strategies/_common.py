"""Shared pure helpers for lab strategies (no I/O, no broker)."""
from __future__ import annotations

import pandas as pd


def stop_fill_price(stop_level: float, bar_open: float) -> float:
    """Realistic stop fill for a long: min(stop, open) on gap-downs."""
    return min(stop_level, bar_open)


def is_fresh_breakout(
    highs: list[float],
    closes: list[float],
    i: int,
    lookback: int,
) -> bool:
    """True iff bar i is the FIRST bar of a new lookback-bar high.

    Conditions:
      1. closes[i] >= max(highs[i-lookback : i])
      2. closes[i-1] < max(highs[prev_start : i-1])
    """
    window_cur_max = max(highs[i - lookback: i])
    if closes[i] < window_cur_max:
        return False
    prev_start = max(0, i - 1 - lookback)
    window_prev_max = max(highs[prev_start: i - 1])
    return closes[i - 1] < window_prev_max


def build_risk_on(spy_df: pd.DataFrame, sma_period: int = 200) -> dict:
    """Causal risk-on map: date -> (SPY close > SMA). Warmup NaN => risk-OFF."""
    sma = spy_df["close"].rolling(sma_period).mean().to_numpy()
    closes = spy_df["close"].to_numpy()
    return {
        ts.date(): bool(not pd.isna(sma[i]) and closes[i] > sma[i])
        for i, ts in enumerate(spy_df.index)
    }
