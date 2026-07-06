"""Tests for runners/runner_strategy.py — written FIRST (TDD red phase).

Pure functions over a 1-min session DataFrame (ET DatetimeIndex, OHLCV).
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from scripts.research.runners.runner_strategy import (
    track_hod,
    detect_coil,
    entry_signal,
    simulate_trade,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(rows: list[dict], start: str = "2025-01-02 09:30") -> pd.DataFrame:
    """Build a 1-min intraday OHLCV frame with an ET DatetimeIndex."""
    df = pd.DataFrame(rows)
    df.index = pd.date_range(start, periods=len(df), freq="1min", tz="America/New_York")
    return df


def _bar(o, h, l, c, v=100_000) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# ---------------------------------------------------------------------------
# track_hod
# ---------------------------------------------------------------------------

def test_track_hod_is_running_max_of_high():
    df = _make_session([
        _bar(10.0, 10.5, 9.9, 10.2),
        _bar(10.2, 10.4, 10.0, 10.1),   # lower high -> HOD stays 10.5
        _bar(10.1, 11.0, 10.0, 10.8),   # new high -> HOD 11.0
        _bar(10.8, 10.9, 10.5, 10.7),   # lower high -> HOD stays 11.0
    ])
    hod = track_hod(df)
    assert list(hod) == [10.5, 10.5, 11.0, 11.0]
    # returns a Series aligned to the frame's index
    assert isinstance(hod, pd.Series)
    assert list(hod.index) == list(df.index)


# ---------------------------------------------------------------------------
# detect_coil
# ---------------------------------------------------------------------------

def test_detect_coil_true_for_tight_range_at_highs():
    """3 tight bars riding the highs before bar i -> coil."""
    df = _make_session([
        _bar(9.0, 9.5, 8.9, 9.4),       # earlier bar, lower highs
        _bar(9.4, 10.00, 9.9, 9.98),    # coil bar (window start), pushes to highs
        _bar(9.98, 10.05, 9.95, 10.0),  # coil bar, tight
        _bar(10.0, 10.06, 9.97, 10.02), # coil bar, tight
        _bar(10.02, 10.5, 10.0, 10.4),  # bar i = candidate breakout (not part of coil)
    ])
    # coil = bars [1,2,3] (n_bars=3) immediately before i=4
    assert detect_coil(df, i=4, n_bars=3, max_range_pct=0.03) is True


def test_detect_coil_false_when_range_too_wide():
    df = _make_session([
        _bar(9.0, 9.5, 8.9, 9.4),
        _bar(9.4, 10.0, 9.0, 9.9),      # wide bar
        _bar(9.9, 10.1, 9.2, 10.0),     # wide bar
        _bar(10.0, 10.2, 9.3, 10.1),    # wide bar -> range ~ (10.2-9.0)/9.0 = 13%
        _bar(10.1, 10.5, 10.0, 10.4),
    ])
    assert detect_coil(df, i=4, n_bars=3, max_range_pct=0.03) is False


def test_detect_coil_false_when_not_at_hod():
    """Tight consolidation, but well below the day's earlier high -> not a coil at HOD."""
    df = _make_session([
        _bar(12.0, 13.0, 11.9, 12.1),   # big earlier high 13.0
        _bar(10.0, 10.05, 9.95, 10.0),  # tight, but far below HOD
        _bar(10.0, 10.06, 9.96, 10.01),
        _bar(10.01, 10.05, 9.97, 10.0),
        _bar(10.0, 10.5, 9.98, 10.4),
    ])
    assert detect_coil(df, i=4, n_bars=3, max_range_pct=0.03) is False


# ---------------------------------------------------------------------------
# entry_signal
# ---------------------------------------------------------------------------

def test_entry_signal_true_on_close_above_coil_with_volume_surge():
    df = _make_session([
        _bar(9.0, 9.5, 8.9, 9.4, v=100_000),
        _bar(9.4, 10.00, 9.9, 9.98, v=100_000),   # coil, avg vol 100k
        _bar(9.98, 10.05, 9.95, 10.0, v=100_000),
        _bar(10.0, 10.06, 9.97, 10.02, v=100_000),
        _bar(10.02, 10.5, 10.0, 10.4, v=250_000),  # close 10.4 > coil_high 10.06, vol 2.5x
    ])
    assert entry_signal(df, i=4, n_bars=3, max_range_pct=0.03, vol_mult=2.0) is True


def test_entry_signal_false_without_volume_surge():
    df = _make_session([
        _bar(9.0, 9.5, 8.9, 9.4, v=100_000),
        _bar(9.4, 10.00, 9.9, 9.98, v=100_000),
        _bar(9.98, 10.05, 9.95, 10.0, v=100_000),
        _bar(10.0, 10.06, 9.97, 10.02, v=100_000),
        _bar(10.02, 10.5, 10.0, 10.4, v=120_000),  # price breaks but vol only 1.2x
    ])
    assert entry_signal(df, i=4, n_bars=3, max_range_pct=0.03, vol_mult=2.0) is False


def test_entry_signal_false_when_close_not_above_coil_high():
    df = _make_session([
        _bar(9.0, 9.5, 8.9, 9.4, v=100_000),
        _bar(9.4, 10.00, 9.9, 9.98, v=100_000),
        _bar(9.98, 10.05, 9.95, 10.0, v=100_000),
        _bar(10.0, 10.06, 9.97, 10.02, v=100_000),
        _bar(10.02, 10.5, 10.0, 10.05, v=300_000),  # high pokes up but CLOSE 10.05 <= coil_high 10.06
    ])
    assert entry_signal(df, i=4, n_bars=3, max_range_pct=0.03, vol_mult=2.0) is False


# ---------------------------------------------------------------------------
# simulate_trade
# ---------------------------------------------------------------------------

def _exit_session(rows: list[dict], start: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.date_range(start, periods=len(df), freq="1min", tz="America/New_York")
    return df


def test_simulate_trade_scale_half_at_1R_then_flatten_eod():
    """Half exits at +1R (limit); remainder flattens at EOD close.

    entry close 10.0, coil_low 9.0 -> R=1.0, target1=11.0, BE stop=10.0.
    Short frame => ATR is NaN => chandelier inactive; only BE stop + EOD apply.
    """
    df = _exit_session([
        _bar(10.0, 10.1, 9.9, 10.0),    # 15:50 entry bar (entry_i=0)
        _bar(10.0, 11.2, 10.1, 11.0),   # 15:51 hits target1=11.0 -> scale half, stop->10.0
        _bar(11.0, 11.5, 10.8, 11.3),   # 15:52
        _bar(11.3, 11.8, 11.0, 11.6),   # 15:53
        _bar(11.6, 12.0, 11.2, 11.9),   # 15:54
        _bar(11.9, 12.0, 11.5, 11.8),   # 15:55 EOD flatten -> remainder @ close 11.8
    ], start="2025-01-02 15:50")

    t = simulate_trade(df, entry_i=0, coil_low=9.0, slip_bps=0.0)

    assert t["reason"] == "eod"
    assert t["exit_avg"] == pytest.approx(11.4)          # 0.5*11.0 + 0.5*11.8
    assert t["r_multiple"] == pytest.approx(1.4)         # (11.4-10.0)/1.0
    assert t["return_pct"] == pytest.approx(0.14)
    assert len(t["exits"]) == 2
    assert t["exits"][0]["reason"] == "scale1"


def test_simulate_trade_full_stop_out_is_minus_1R():
    """Never reaches +1R; low pierces coil_low -> full stop at -1R."""
    df = _exit_session([
        _bar(10.0, 10.1, 9.9, 10.0),    # entry
        _bar(9.5, 10.5, 8.8, 9.2),      # low 8.8 <= stop 9.0; open 9.5 => fill=min(9.0,9.5)=9.0
    ], start="2025-01-02 15:50")

    t = simulate_trade(df, entry_i=0, coil_low=9.0, slip_bps=0.0)

    assert t["reason"] == "stop"
    assert t["exit_avg"] == pytest.approx(9.0)
    assert t["r_multiple"] == pytest.approx(-1.0)
    assert t["return_pct"] == pytest.approx(-0.10)


def test_simulate_trade_gap_down_fills_below_stop():
    """Bar opens below the stop (gap) -> fill at the open, not the stop."""
    df = _exit_session([
        _bar(10.0, 10.1, 9.9, 10.0),
        _bar(8.5, 8.6, 8.0, 8.2),       # gap open 8.5 < stop 9.0 => fill=min(9.0,8.5)=8.5
    ], start="2025-01-02 15:50")

    t = simulate_trade(df, entry_i=0, coil_low=9.0, slip_bps=0.0)

    assert t["reason"] == "stop"
    assert t["exit_avg"] == pytest.approx(8.5)
    assert t["r_multiple"] == pytest.approx(-1.5)         # (8.5-10.0)/1.0


def test_simulate_trade_applies_slippage_both_sides():
    """slip 100 bps: buy fills higher, sell fills lower, in the return math."""
    df = _exit_session([
        _bar(10.0, 10.1, 9.9, 10.0),
        _bar(9.5, 10.5, 8.8, 9.2),      # stop-out at raw fill 9.0
    ], start="2025-01-02 15:50")

    t = simulate_trade(df, entry_i=0, coil_low=9.0, slip_bps=100.0)

    # entry_fill = 10.0*1.01 = 10.1 ; sell_fill = 9.0*0.99 = 8.91
    assert t["entry"] == pytest.approx(10.1)
    assert t["exit_avg"] == pytest.approx(8.91)
    assert t["return_pct"] == pytest.approx(8.91 / 10.1 - 1)
    assert t["r_multiple"] == pytest.approx((8.91 - 10.1) / 1.0)
