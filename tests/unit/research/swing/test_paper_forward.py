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


# ---------------------------------------------------------------------------
# step — exits and idempotency
# ---------------------------------------------------------------------------

def _state_with_open_position(
    symbol: str = "SYM",
    entry_price: float = 100.0,
    stop_pct: float = 0.08,
    notional: float = 25.0,
    equity: float = 200.0,
    entry_date: str = "2025-01-15",
) -> dict:
    """Return a state dict that already has one open position."""
    stop_price = entry_price * (1 - stop_pct)
    s = new_state(starting_equity=equity)
    s["available_cash"] = equity - notional
    s["open_positions"] = [{
        "symbol": symbol,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "notional": notional,
    }]
    return s


def _make_exit_df(
    close_today: float,
    low_today: float,
    ma_exit: int = 3,
    n_seed: int = 5,
    seed_close: float = 100.0,
) -> pd.DataFrame:
    """Frame with n_seed seed bars then one 'today' bar.

    Seed bars have close=seed_close so SMA(ma_exit) is well-defined by today.
    today bar has the provided close_today and low_today.
    """
    rows = []
    for _ in range(n_seed):
        rows.append({
            "open": seed_close,
            "high": seed_close + 1,
            "low": seed_close - 1,
            "close": seed_close,
            "volume": 1_000_000,
        })
    rows.append({
        "open": close_today - 0.5,
        "high": close_today + 1,
        "low": low_today,
        "close": close_today,
        "volume": 1_000_000,
    })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-13", periods=len(rows), freq="B", tz="America/New_York")
    return df


def test_step_closes_position_on_hard_stop():
    """When today's low <= stop_price, position exits at stop_price."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    entry_price = 100.0
    stop_pct = 0.08
    stop_price = entry_price * (1 - stop_pct)  # 92.0
    notional = 25.0

    s = _state_with_open_position(
        entry_price=entry_price, stop_pct=stop_pct, notional=notional,
        equity=200.0, entry_date="2025-01-15",
    )
    # today bar: low=91.0 <= 92.0 → stop triggered
    df = _make_exit_df(close_today=93.0, low_today=91.0, ma_exit=3, seed_close=100.0)
    today = df.index[-1].date()  # 2025-01-21

    s2 = step(s, {"SYM": df}, today, risk_pct=0.01, lookback=3, ma_exit=3,
              stop_pct=stop_pct, slip_bps=slip_bps)

    assert len(s2["open_positions"]) == 0, "Position should be closed"
    assert len(s2["closed_trades"]) == 1
    t = s2["closed_trades"][0]
    assert t["reason"] == "stop"
    assert abs(t["exit_price"] - stop_price) < 1e-9

    expected_pnl = notional * ((stop_price * (1 - slip)) / (entry_price * (1 + slip)) - 1)
    assert abs(t["pnl"] - expected_pnl) < 1e-9
    assert expected_pnl < 0.0  # stop is a loss

    # cash returns: available_cash = initial_cash + notional + pnl
    initial_cash = 200.0 - notional
    assert abs(s2["available_cash"] - (initial_cash + notional + expected_pnl)) < 1e-9


def test_step_closes_position_on_trend_break():
    """When close < SMA(ma_exit), position exits at today's close."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    entry_price = 100.0
    stop_pct = 0.08
    notional = 25.0
    ma_exit = 3

    s = _state_with_open_position(
        entry_price=entry_price, stop_pct=stop_pct, notional=notional,
        equity=200.0, entry_date="2025-01-15",
    )
    # Seed close=100.0 for 5 bars, then today close=85.0.
    # SMA(3) at today = (100+100+85)/3=95.0; 85 < 95 → trend_break.
    # low_today=93.0 > stop_price=92.0 → stop does NOT fire first.
    df = _make_exit_df(close_today=85.0, low_today=93.0, ma_exit=ma_exit, seed_close=100.0)
    today = df.index[-1].date()

    s2 = step(s, {"SYM": df}, today, risk_pct=0.01, lookback=3, ma_exit=ma_exit,
              stop_pct=stop_pct, slip_bps=slip_bps)

    assert len(s2["open_positions"]) == 0
    assert len(s2["closed_trades"]) == 1
    t = s2["closed_trades"][0]
    assert t["reason"] == "trend_break"
    assert abs(t["exit_price"] - 85.0) < 1e-9

    expected_pnl = notional * ((85.0 * (1 - slip)) / (entry_price * (1 + slip)) - 1)
    assert abs(t["pnl"] - expected_pnl) < 1e-9


def test_step_stop_checked_before_trend_break():
    """When BOTH stop AND close < SMA are true, reason is 'stop' (stop checked first)."""
    entry_price = 100.0
    stop_pct = 0.08
    stop_price = entry_price * (1 - stop_pct)  # 92.0
    notional = 25.0

    s = _state_with_open_position(
        entry_price=entry_price, stop_pct=stop_pct, notional=notional, equity=200.0
    )
    # low=80.0 <= 92.0 (stop fires); close=80.0 < SMA (trend_break also true)
    df = _make_exit_df(close_today=80.0, low_today=80.0, ma_exit=3, seed_close=100.0)
    today = df.index[-1].date()

    s2 = step(s, {"SYM": df}, today, risk_pct=0.01, lookback=3, ma_exit=3,
              stop_pct=stop_pct, slip_bps=15.0)

    assert s2["closed_trades"][0]["reason"] == "stop"


def test_step_idempotent_same_day():
    """Calling step twice with the same today must not double-process."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()
    bars = {"SYM": df}

    s = new_state(starting_equity=200.0)
    s1 = step(s, bars, today, lookback=lookback, ma_exit=3, stop_pct=0.08)
    open_count_after_1 = len(s1["open_positions"])
    cash_after_1 = s1["available_cash"]

    # Second call with same day — should be a no-op
    s2 = step(s1, bars, today, lookback=lookback, ma_exit=3, stop_pct=0.08)
    assert len(s2["open_positions"]) == open_count_after_1
    assert s2["available_cash"] == cash_after_1
    assert len(s2["closed_trades"]) == 0


def test_step_idempotent_earlier_day():
    """Calling step with today < last_date returns state unchanged."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()
    yesterday = today - datetime.timedelta(days=1)
    bars = {"SYM": df}

    s = new_state(starting_equity=200.0)
    s1 = step(s, bars, today, lookback=lookback, ma_exit=3, stop_pct=0.08)
    s2 = step(s1, bars, yesterday, lookback=lookback, ma_exit=3, stop_pct=0.08)

    # No change
    assert len(s2["open_positions"]) == len(s1["open_positions"])
    assert s2["available_cash"] == s1["available_cash"]


# ---------------------------------------------------------------------------
# Sizing edge cases
# ---------------------------------------------------------------------------

def test_step_notional_capped_at_available_cash():
    """When risk_pct*equity/stop_pct > available_cash, notional = available_cash."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()

    # equity=200, risk_pct=0.5, stop_pct=0.01 → uncapped notional=200*0.5/0.01=10000
    # but available_cash=200 → capped at 200
    s = new_state(starting_equity=200.0)
    s2 = step(s, {"SYM": df}, today, risk_pct=0.5, lookback=lookback,
              ma_exit=3, stop_pct=0.01, slip_bps=0.0)

    assert len(s2["open_positions"]) == 1
    pos = s2["open_positions"][0]
    assert abs(pos["notional"] - 200.0) < 1e-9
    assert abs(s2["available_cash"]) < 1e-9


def test_step_skips_entry_when_notional_below_1():
    """When notional would be < 1.0, step skips entry and leaves cash unchanged."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()

    # equity=0.05, risk_pct=0.01, stop_pct=0.08 → notional = 0.05*0.01/0.08=0.00625 < 1
    s = new_state(starting_equity=0.05)
    s2 = step(s, {"SYM": df}, today, risk_pct=0.01, lookback=lookback,
              ma_exit=3, stop_pct=0.08, slip_bps=0.0)

    assert len(s2["open_positions"]) == 0
    assert abs(s2["available_cash"] - 0.05) < 1e-9


def test_step_stop_gap_down_fills_at_open():
    """A gap-down through the stop fills at the bar's open, not the stop price."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    entry_price = 100.0
    stop_pct = 0.08
    stop_price = entry_price * (1 - stop_pct)  # 92.0
    notional = 25.0

    s = _state_with_open_position(
        entry_price=entry_price, stop_pct=stop_pct, notional=notional, equity=200.0,
    )
    # today gaps down: open = close_today - 0.5 = 87.5 (< stop 92), low 85 <= stop.
    df = _make_exit_df(close_today=88.0, low_today=85.0, ma_exit=3, seed_close=100.0)
    today = df.index[-1].date()
    open_today = float(df["open"].iloc[-1])  # 87.5
    assert open_today < stop_price  # sanity: this really is a gap-down

    s2 = step(s, {"SYM": df}, today, risk_pct=0.01, lookback=3, ma_exit=3,
              stop_pct=stop_pct, slip_bps=slip_bps)

    assert len(s2["closed_trades"]) == 1
    t = s2["closed_trades"][0]
    assert t["reason"] == "stop"
    # Filled at the open, NOT the (better) stop level.
    assert abs(t["exit_price"] - open_today) < 1e-9
    expected_pnl = notional * ((open_today * (1 - slip)) / (entry_price * (1 + slip)) - 1)
    assert abs(t["pnl"] - expected_pnl) < 1e-9
