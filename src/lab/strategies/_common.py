"""Shared pure helpers for lab strategies (no I/O, no broker)."""
from __future__ import annotations

import pandas as pd



def true_range_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """SMA of true range, including the gap term from the prior close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def atr_stop_distance(
    atr: float,
    entry: float,
    k1: float,
    lo: float = 0.04,
    hi: float = 0.15,
) -> float:
    """clamp(k1 * ATR / entry, lo, hi). Degenerate inputs bind to lo."""
    if entry <= 0 or atr <= 0 or k1 <= 0:
        return float(lo)
    raw = k1 * atr / entry
    return float(max(lo, min(hi, raw)))


def chandelier_floor(highest_high: float, atr: float, k2: float) -> float:
    """highest_high − k2 × ATR. Never used below the initial stop — caller max()s."""
    return float(highest_high - k2 * atr)

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


def regime_snapshot(spy_df, sma_period: int = 200, *, as_of=None) -> dict | None:
    """Regime at *as_of*: risk-on flag and SPY's distance from its SMA.

    Returns None when the regime is genuinely unknown — no SPY frame, the date
    is absent, or the SMA is still in warmup — so a missing regime is never
    recorded as a neutral 0.0.
    """
    if spy_df is None or getattr(spy_df, "empty", True):
        return None
    i = None
    for j, ts in enumerate(spy_df.index):
        if as_of is None or ts.date() <= as_of:
            i = j
    if i is None:
        return None
    sma = spy_df["close"].rolling(sma_period).mean().to_numpy()
    if pd.isna(sma[i]) or sma[i] <= 0:
        return None
    close = float(spy_df["close"].to_numpy()[i])
    avg = float(sma[i])
    return {"risk_on": bool(close > avg), "spy_vs_sma200": close / avg - 1.0}
