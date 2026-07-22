"""Dual-run: lab SimFill path matches historical paper_forward.step semantics."""
from __future__ import annotations

import copy

import pandas as pd

from scripts.research.swing.paper_forward import new_state, step


def _make_breakout_bars(lookback: int = 10) -> pd.DataFrame:
    n = lookback + 1
    rows = []
    for i in range(n):
        if i == lookback - 1:
            c, h = 99.0, 99.0
        elif i == lookback:
            c, h = 101.0, 102.0
        else:
            c, h = 100.0, 100.0
        rows.append(
            {"open": c - 0.1, "high": h, "low": c - 0.5, "close": c, "volume": 1_000_000}
        )
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=n, freq="B", tz="America/New_York")
    return df


def test_step_entry_cash_and_notional():
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()
    s = new_state(200.0)
    s2 = step(s, {"SYM": df}, today, lookback=lookback, ma_exit=3, stop_pct=0.08)
    assert len(s2["open_positions"]) == 1
    assert abs(s2["open_positions"][0]["notional"] - 25.0) < 0.01
    assert abs(s2["available_cash"] - 175.0) < 0.01
    assert "equity_curve_daily" in s2
    assert s2["equity_curve_daily"][-1]["date"] == today.isoformat()


def test_two_day_step_stop_exit_pnl():
    """Enter on breakout day, stop out next session — PnL within $0.01 of formula."""
    lookback = 10
    df = _make_breakout_bars(lookback)
    day1 = df.index[-1].date()
    s = step(new_state(200.0), {"SYM": df}, day1, lookback=lookback, ma_exit=3, stop_pct=0.08)
    entry = s["open_positions"][0]["entry_price"]
    stop = s["open_positions"][0]["stop_price"]
    notional = s["open_positions"][0]["notional"]

    # Next business day: gap through stop
    next_idx = df.index[-1] + pd.offsets.BDay(1)
    row = {
        "open": stop - 1.0,
        "high": stop,
        "low": stop - 2.0,
        "close": stop - 0.5,
        "volume": 1_000_000,
    }
    df2 = pd.concat([df, pd.DataFrame([row], index=[next_idx])])
    day2 = next_idx.date()
    s2 = step(copy.deepcopy(s), {"SYM": df2}, day2, lookback=lookback, ma_exit=3, stop_pct=0.08)
    assert len(s2["open_positions"]) == 0
    assert len(s2["closed_trades"]) == 1
    t = s2["closed_trades"][0]
    assert t["reason"] == "stop"
    slip = 2 * 15.0 / 10_000
    exit_px = min(stop, stop - 1.0)  # stop_fill
    expected = notional * ((exit_px * (1 - slip)) / (entry * (1 + slip)) - 1)
    assert abs(t["pnl"] - expected) < 0.01
    assert abs(s2["available_cash"] - (175.0 + notional + expected)) < 0.01
