"""Unit tests for paper_forward.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.research.swing.paper_forward import (
    new_state,
    load_state,
    save_state,
    is_fresh_breakout,
    step,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(rows: list[dict], start: str = "2025-01-02") -> pd.DataFrame:
    """Minimal daily OHLCV DataFrame with a DatetimeIndex (tz-aware, business freq)."""
    df = pd.DataFrame(rows)
    df.index = pd.date_range(start, periods=len(df), freq="B", tz="America/New_York")
    return df


# ---------------------------------------------------------------------------
# new_state / save_state / load_state
# ---------------------------------------------------------------------------

def test_new_state_default_equity():
    s = new_state()
    assert s["starting_equity"] == 200.0
    assert s["available_cash"] == 200.0
    assert s["realized_pnl"] == 0.0
    assert s["last_date"] is None
    assert s["open_positions"] == []
    assert s["closed_trades"] == []


def test_new_state_custom_equity():
    s = new_state(starting_equity=500.0)
    assert s["starting_equity"] == 500.0
    assert s["available_cash"] == 500.0


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    s = new_state(starting_equity=300.0)
    s["realized_pnl"] = 12.34
    s["last_date"] = "2025-03-01"
    save_state(str(path), s)

    loaded = load_state(str(path))
    assert loaded["starting_equity"] == 300.0
    assert abs(loaded["realized_pnl"] - 12.34) < 1e-9
    assert loaded["last_date"] == "2025-03-01"


def test_load_state_missing_file_returns_new_state(tmp_path):
    path = tmp_path / "nonexistent.json"
    s = load_state(str(path))
    assert s["starting_equity"] == 200.0
    assert s["last_date"] is None


def test_save_state_pretty_json(tmp_path):
    path = tmp_path / "pretty.json"
    s = new_state()
    save_state(str(path), s)
    text = path.read_text()
    # pretty-printed JSON has newlines
    assert "\n" in text
    # valid JSON
    loaded = json.loads(text)
    assert loaded["starting_equity"] == 200.0


# ---------------------------------------------------------------------------
# is_fresh_breakout
# ---------------------------------------------------------------------------

def _make_breakout_arrays(lookback: int = 10):
    """Return (highs, closes) for is_fresh_breakout tests.

    Bars 0-8:  high=close=100 (seed — all below 100-level after we tweak bar 9).
    Bar 9:     high=99, close=99   ← slightly lower so close[9] < max(high[0:9])=100.
    Bar 10:    high=102, close=101 ← fresh breakout: close >= max(high[0:10])=100
                                      AND close[9]=99 < max(high[0:9])=100.
    Bar 11:    high=103, close=102 ← continuation: prev bar (10) WAS already at new high.
    Bar 12:    high=90,  close=90  ← below prior highs, clearly not a breakout.
    """
    highs  = [100.0]*9 + [99.0, 102.0, 103.0, 90.0]
    closes = [100.0]*9 + [99.0, 101.0, 102.0, 90.0]
    return highs, closes


def test_is_fresh_breakout_true_on_first_new_high():
    highs, closes = _make_breakout_arrays()
    # i=10 is the first fresh breakout bar
    assert is_fresh_breakout(highs, closes, i=10, lookback=10) is True


def test_is_fresh_breakout_false_on_continuation():
    highs, closes = _make_breakout_arrays()
    # i=11: close[10]=101 >= max(high[0:10])=100 → prior bar already at new high
    assert is_fresh_breakout(highs, closes, i=11, lookback=10) is False


def test_is_fresh_breakout_false_below_prior_highs():
    highs, closes = _make_breakout_arrays()
    # i=12: close=90 < max(high[2:12])=103 → not at new high at all
    assert is_fresh_breakout(highs, closes, i=12, lookback=10) is False


def test_is_fresh_breakout_guard_start_of_array():
    """When i-lookback-1 < 0, the prior-window check uses max(high[0:i-1])."""
    # Use a short array where i-lookback-1 would be negative
    highs  = [100.0, 99.0, 101.0]
    closes = [100.0, 99.0, 101.0]
    # i=2, lookback=2: window_cur = high[0:2]=[100,99], max=100; close[2]=101>=100 ✓
    # prior window: i-1-lookback = 2-1-2=-1 → guard to 0, max(high[0:1])=[100], max=100
    # close[1]=99 < 100 → fresh breakout
    assert is_fresh_breakout(highs, closes, i=2, lookback=2) is True


# ---------------------------------------------------------------------------
# step — entries
# ---------------------------------------------------------------------------

def _make_breakout_bars(lookback: int = 10) -> pd.DataFrame:
    """DataFrame with a fresh breakout at the last bar.

    Bars 0..lookback-2 (9 bars): high=close=100.
    Bar lookback-1 (bar 9): high=99, close=99 (so close[9] < max(high[0:9])=100).
    Bar lookback (bar 10): high=102, close=101 — fresh breakout.
    """
    n = lookback + 1  # 11 bars (indices 0..10)
    rows = []
    for i in range(n):
        if i == lookback - 1:
            c, h = 99.0, 99.0
        elif i == lookback:
            c, h = 101.0, 102.0
        else:
            c, h = 100.0, 100.0
        rows.append({"open": c - 0.1, "high": h, "low": c - 0.5, "close": c, "volume": 1_000_000})
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=n, freq="B", tz="America/New_York")
    return df


def test_step_opens_position_on_fresh_breakout():
    """step() opens a position on the fresh-breakout bar and deducts cash."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    # today = last bar's date
    today = df.index[-1].date()
    bars = {"SYM": df}

    s = new_state(starting_equity=200.0)
    s2 = step(s, bars, today, risk_pct=0.01, lookback=lookback, ma_exit=3,
              stop_pct=0.08, slip_bps=15.0)

    assert len(s2["open_positions"]) == 1, f"Expected 1 open position, got {s2['open_positions']}"
    pos = s2["open_positions"][0]
    assert pos["symbol"] == "SYM"
    assert pos["entry_date"] == today.isoformat()
    # entry_price = close of breakout bar = 101.0
    assert abs(pos["entry_price"] - 101.0) < 1e-9
    # stop_price = entry * (1 - 0.08)
    assert abs(pos["stop_price"] - 101.0 * 0.92) < 1e-9
    # available_cash decreased
    assert s2["available_cash"] < 200.0
    # notional = min(risk_pct*equity/stop_pct, available_cash)
    equity = 200.0
    expected_notional = min(0.01 * equity / 0.08, 200.0)  # = 25.0
    assert abs(pos["notional"] - expected_notional) < 1e-9
    assert abs(s2["available_cash"] - (200.0 - expected_notional)) < 1e-9


def test_step_does_not_duplicate_existing_position():
    """If a position is already open for a symbol, step skips entry even on breakout."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()
    bars = {"SYM": df}

    s = new_state(starting_equity=200.0)
    # Pre-plant an open position for SYM
    s["open_positions"].append({
        "symbol": "SYM",
        "entry_date": "2025-01-01",
        "entry_price": 50.0,
        "stop_price": 46.0,
        "notional": 25.0,
    })
    s["available_cash"] = 175.0

    s2 = step(s, bars, today, risk_pct=0.01, lookback=lookback, ma_exit=3,
              stop_pct=0.08, slip_bps=15.0)

    # Still exactly 1 open position (no new entry)
    assert len(s2["open_positions"]) == 1
    assert s2["available_cash"] == 175.0
