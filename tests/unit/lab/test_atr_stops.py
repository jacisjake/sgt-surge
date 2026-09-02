"""ATR stop distance, clamp, and chandelier floor — convex-breakout risk model."""
import pandas as pd

from src.lab.strategies._common import (
    atr_stop_distance,
    chandelier_floor,
    true_range_atr,
)


def test_atr_stop_distance_is_k1_times_atr_pct():
    # ATR 2 on a $20 name, k1=2 → 20% raw, but clamp max is 15%
    assert atr_stop_distance(atr=1.0, entry=20.0, k1=2.0) == 0.10


def test_atr_stop_distance_clamps_to_four_percent():
    assert atr_stop_distance(atr=0.10, entry=20.0, k1=2.0) == 0.04


def test_atr_stop_distance_clamps_to_fifteen_percent():
    assert atr_stop_distance(atr=4.0, entry=20.0, k1=2.0) == 0.15


def test_atr_stop_distance_keeps_one_percent_risk_across_atrs():
    """qty = (risk_pct * equity) / (entry * stop_dist); dollar risk stays flat."""
    equity, risk_pct, entry = 200.0, 0.01, 10.0
    for atr in (0.2, 0.5, 1.0, 2.0, 5.0):
        dist = atr_stop_distance(atr, entry, k1=2.0)
        qty = (risk_pct * equity) / (entry * dist)
        dollar_risk = qty * entry * dist
        assert abs(dollar_risk - 2.0) < 1e-9


def test_true_range_atr_includes_gap_from_prior_close():
    # bar0: TR = high-low = 1 (no prior close).
    # bar1: gaps up from close 10 → TR = max(1, 4, 3) = 4
    # bar2: TR = max(1, |12-13|, |11-13|) = 2
    # ATR(2) at bar2 = (4+2)/2 = 3.  high-low-only ATR would be 1.
    df = pd.DataFrame(
        {
            "high": [11.0, 14.0, 12.0],
            "low": [10.0, 13.0, 11.0],
            "close": [10.0, 13.0, 11.5],
        }
    )
    atr = true_range_atr(df, period=2)
    assert abs(float(atr.iloc[-1]) - 3.0) < 1e-9
    high_low_only = (df["high"] - df["low"]).rolling(2).mean()
    assert float(high_low_only.iloc[-1]) == 1.0


def test_chandelier_floor_is_highest_high_minus_k2_atr():
    assert chandelier_floor(highest_high=13.0, atr=0.3, k2=3.0) == 12.1
