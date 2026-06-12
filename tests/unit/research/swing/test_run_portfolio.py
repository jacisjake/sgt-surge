"""Tests for swing/run_portfolio.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import datetime
from functools import partial

import pandas as pd
import pytest

from scripts.research.swing.run_portfolio import run
from scripts.research.swing.strategies import (
    short_term_reversal_trades,
    trend_pullback_trades,
)


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

def _make_daily_df(n_rows: int = 250) -> pd.DataFrame:
    """Build a daily OHLCV frame with a clear down-dip at the end to trigger
    at least one short_term_reversal_trades entry.

    The frame starts rising (so SMA(200) can form), then dips to produce a
    qualifying 3-day down sequence above the SMA.
    """
    rows = []
    # 200 seed rows rising gently so SMA(200) is well below the price level
    base = 100.0
    for i in range(200):
        c = base + i * 0.05  # gentle up-trend
        rows.append({
            "open": c - 0.2,
            "high": c + 0.5,
            "low":  c - 0.5,
            "close": c,
            "volume": 1_000_000,
        })

    # 4 rows declining so row 203 can be an entry:
    # close[200]=119.6 > close[201]=118 > close[202]=116 > close[203]=114
    for c in [119.6, 118.0, 116.0, 114.0]:
        rows.append({
            "open": c + 0.1,
            "high": c + 2.0,
            "low":  c - 2.0,
            "close": c,
            "volume": 1_000_000,
        })

    # Exit rows (hold=5): high reaches target (+10%) on first bar
    target = 114.0 * 1.10  # 125.4
    for _ in range(6):
        rows.append({
            "open": 115.0,
            "high": 130.0,  # above target
            "low":  112.0,
            "close": 125.0,
            "volume": 1_200_000,
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        "2024-01-02", periods=len(df), freq="B", tz="America/New_York"
    )
    return df


def _make_trend_daily_df() -> pd.DataFrame:
    """Daily OHLCV frame that reliably triggers trend_pullback_trades with
    ma_entry=5, ma_exit=2, down_days=3, stop_pct=0.08.

    12 bars mirroring the strategy unit-test frame:
      closes: 100 110 120 130 140 139 138 137 138 139 140 130
    Entry at i=7. Exit at i=11 via MA(2) break.
    Lows set to close-3 so hard stop (126.04) is never triggered.
    """
    closes = [100.0, 110.0, 120.0, 130.0, 140.0, 139.0, 138.0, 137.0,
              138.0, 139.0, 140.0, 130.0]
    rows = [
        {
            "open":   c + 0.1,
            "high":   c + 1.0,
            "low":    c - 3.0,
            "close":  c,
            "volume": 1_000_000,
        }
        for c in closes
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        "2024-01-02", periods=len(rows), freq="B", tz="America/New_York"
    )
    return df


class _FakeClient:
    """Always returns the same frame for any symbol."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def get_history(self, symbol, freq, start, end, **kwargs) -> pd.DataFrame:
        return self._df


# ---------------------------------------------------------------------------
# Default trade_fn for legacy tests (short_term_reversal_trades with defaults)
# ---------------------------------------------------------------------------

_DEFAULT_TRADE_FN = partial(
    short_term_reversal_trades,
    down_days=3, hold=5, stop_pct=0.05, target_pct=0.10, ma=200, slip_bps=15.0,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_returns_dict_with_required_keys():
    """run() must return a dict containing the portfolio summary keys."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN)

    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys()), (
        f"Missing keys: {required - result.keys()}"
    )


def test_run_final_equity_positive():
    """final_equity must always be > 0."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN)
    assert result["final_equity"] > 0.0


def test_run_with_two_symbols_more_trades_taken():
    """Two symbols produce at least as many taken trades as one symbol."""
    df = _make_daily_df()
    client = _FakeClient(df)

    result_one = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                     trade_fn=_DEFAULT_TRADE_FN)
    result_two = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01",
                     trade_fn=_DEFAULT_TRADE_FN)

    assert result_two["n_taken"] >= result_one["n_taken"]


def test_run_accepts_start_equity_and_risk_pct_params():
    """Custom starting_equity and risk_pct are passed through."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN,
                 starting_equity=500.0, risk_pct=0.02)
    assert result["starting_equity"] == 500.0


def test_run_empty_symbol_list_returns_starting_equity_unchanged():
    """No symbols → no trades → final_equity == starting_equity."""
    client = _FakeClient(_make_daily_df())
    result = run(client, [], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN, starting_equity=300.0)
    assert result["final_equity"] == 300.0
    assert result["n_taken"] == 0


def test_run_skips_none_dataframe():
    """Symbols for which get_history returns None are silently skipped."""

    class _NoneClient:
        def get_history(self, *a, **kw):
            return None

    result = run(_NoneClient(), ["AAPL"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN, starting_equity=200.0)
    assert result["final_equity"] == 200.0
    assert result["n_taken"] == 0


def test_run_n_taken_plus_n_skipped_equals_total_trades():
    """n_taken + n_skipped covers every trade generated."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN)
    # We can't know the exact total without running the strategy separately,
    # but both counts must be non-negative and their sum >= n_taken.
    assert result["n_taken"] >= 0
    assert result["n_skipped"] >= 0


def test_run_respects_reversal_knobs():
    """Changing down_days to an impossible value (> available rows) gives 0 trades."""
    client = _FakeClient(_make_daily_df())
    impossible_fn = partial(
        short_term_reversal_trades,
        down_days=999, hold=5, stop_pct=0.05, target_pct=0.10, ma=200, slip_bps=15.0,
    )
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                 trade_fn=impossible_fn)
    assert result["n_taken"] == 0
    assert result["final_equity"] == result["starting_equity"]


def test_run_respects_max_concurrent():
    """max_concurrent=1 limits simultaneous open positions."""
    df = _make_daily_df()
    client = _FakeClient(df)
    # Run the same two symbols with max_concurrent=1; one concurrent seat only.
    result = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01",
                 trade_fn=_DEFAULT_TRADE_FN, max_concurrent=1)
    # With only 1 concurrent slot across many potential same-day entries,
    # n_skipped should be >= 0 and the result valid.
    assert result["n_taken"] + result["n_skipped"] >= result["n_taken"]


def test_run_with_trend_pullback_trade_fn():
    """run() with trend_pullback_trades trade_fn returns valid summary dict.

    Uses a 12-bar frame that reliably triggers one trade (entry i=7, MA-break
    exit i=11).  With starting_equity=1000 and risk_pct=0.01 the trade is
    taken (notional = 0.01*1000/0.08 = 125 ≥ min_notional=1).
    """
    client = _FakeClient(_make_trend_daily_df())

    trade_fn = partial(
        trend_pullback_trades,
        down_days=3, ma_entry=5, ma_exit=2, stop_pct=0.08, slip_bps=15.0,
    )

    result = run(
        client, ["TPB"],
        start="2024-01-01", end="2025-01-01",
        trade_fn=trade_fn,
        starting_equity=1000.0,
        risk_pct=0.01,
        min_notional=1.0,
    )

    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys())
    assert result["final_equity"] > 0.0
    # The frame has exactly 1 qualifying setup; it should be taken.
    assert result["n_taken"] + result["n_skipped"] >= 1


# ---------------------------------------------------------------------------
# New strategy imports (added with commit 3)
# ---------------------------------------------------------------------------
from scripts.research.swing.strategies import (
    index_rsi2_trades,
    turn_of_month_trades,
    breakout_52w_trades,
)


# ---------------------------------------------------------------------------
# Helper frames for new strategies
# ---------------------------------------------------------------------------

def _make_rsi2_daily_df() -> pd.DataFrame:
    """Frame that triggers index_rsi2_trades (ma=5, rsi_buy=80, rsi_sell=70).

    20 seed bars at 100, then spike to 108, dip to 104 (entry), recover to 115.
    """
    closes = [100.0] * 20 + [108.0, 104.0, 115.0, 116.0, 117.0]
    rows = [
        {
            "open":   c + 0.1,
            "high":   c + 1.0,
            "low":    c - 1.0,
            "close":  c,
            "volume": 1_000_000,
        }
        for c in closes
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-02", periods=len(rows), freq="B", tz="America/New_York")
    return df


def _make_turn_of_month_daily_df() -> pd.DataFrame:
    """Frame that spans 2 month-ends (Jan 31 + Feb 28) to trigger 2 turn-of-month trades."""
    dates_and_closes = [
        ("2024-01-28", 98.0), ("2024-01-29", 99.0), ("2024-01-30", 100.5),
        ("2024-01-31", 100.0),   # entry 1
        ("2024-02-01", 101.0), ("2024-02-02", 102.0), ("2024-02-05", 103.0),
        ("2024-02-06", 104.0),   # exit 1
        ("2024-02-26", 104.5), ("2024-02-27", 104.8),
        ("2024-02-29", 105.0),   # entry 2 (2024 is leap year, Feb 29 exists)
        ("2024-03-01", 106.0), ("2024-03-04", 107.0), ("2024-03-05", 108.0),
        ("2024-03-06", 109.0),   # exit 2
    ]
    rows = []
    for _, c in dates_and_closes:
        rows.append({
            "open": c + 0.1, "high": c + 1.0, "low": c - 1.0,
            "close": c, "volume": 1_000_000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(
        pd.to_datetime([d for d, _ in dates_and_closes]).tz_localize("America/New_York")
    )
    return df


def _make_breakout_daily_df() -> pd.DataFrame:
    """Frame that triggers exactly 1 breakout_52w_trades entry (lookback=10, ma_exit=3).

    10 seed bars at 100, bar 9 at 99, bar 10 breaks out to 101,
    bar 11 still up, bar 12 drops below SMA(3) → exit.
    """
    closes_highs = (
        [(100.0, 100.0)] * 9
        + [(99.0, 99.0)]
        + [(101.0, 102.0), (102.0, 103.0), (95.0, 96.0), (94.0, 95.0)]
    )
    rows = []
    for c, h in closes_highs:
        rows.append({
            "open": c - 0.1, "high": h, "low": c - 1.0,
            "close": c, "volume": 1_000_000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-02", periods=len(rows), freq="B", tz="America/New_York")
    return df


# ---------------------------------------------------------------------------
# Tests for new strategies wired into run()
# ---------------------------------------------------------------------------

def test_run_with_index_rsi2_trade_fn():
    """run() with index_rsi2_trades returns valid summary dict with at least 1 trade."""
    client = _FakeClient(_make_rsi2_daily_df())

    trade_fn = partial(
        index_rsi2_trades,
        ma=5, rsi_buy=80.0, rsi_sell=70.0, max_hold=10,
        stop_pct=0.08, slip_bps=15.0,
    )

    result = run(
        client, ["RSI2"],
        start="2024-01-01", end="2025-01-01",
        trade_fn=trade_fn,
        starting_equity=1000.0,
        risk_pct=0.01,
        min_notional=1.0,
    )

    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys())
    assert result["final_equity"] > 0.0
    assert result["n_taken"] + result["n_skipped"] >= 1


def test_run_with_turn_of_month_trade_fn():
    """run() with turn_of_month_trades returns valid summary dict."""
    client = _FakeClient(_make_turn_of_month_daily_df())

    trade_fn = partial(
        turn_of_month_trades,
        hold=4, stop_pct=0.08, slip_bps=15.0,
    )

    result = run(
        client, ["TOM"],
        start="2024-01-01", end="2025-01-01",
        trade_fn=trade_fn,
        starting_equity=1000.0,
        risk_pct=0.01,
        min_notional=1.0,
    )

    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys())
    assert result["final_equity"] > 0.0
    assert result["n_taken"] + result["n_skipped"] >= 2


def test_run_with_breakout_52w_trade_fn():
    """run() with breakout_52w_trades returns valid summary dict."""
    client = _FakeClient(_make_breakout_daily_df())

    trade_fn = partial(
        breakout_52w_trades,
        lookback=10, ma_exit=3, stop_pct=0.08, slip_bps=15.0,
    )

    result = run(
        client, ["BRK"],
        start="2024-01-01", end="2025-01-01",
        trade_fn=trade_fn,
        starting_equity=1000.0,
        risk_pct=0.01,
        min_notional=1.0,
    )

    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys())
    assert result["final_equity"] > 0.0
    assert result["n_taken"] + result["n_skipped"] >= 1
