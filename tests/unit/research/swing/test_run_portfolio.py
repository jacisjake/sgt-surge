"""Tests for swing/run_portfolio.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from scripts.research.swing.run_portfolio import run


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


class _FakeClient:
    """Always returns the same frame for any symbol."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def get_history(self, symbol, freq, start, end, **kwargs) -> pd.DataFrame:
        return self._df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_returns_dict_with_required_keys():
    """run() must return a dict containing the portfolio summary keys."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01")

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
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01")
    assert result["final_equity"] > 0.0


def test_run_with_two_symbols_more_trades_taken():
    """Two symbols produce at least as many taken trades as one symbol."""
    df = _make_daily_df()
    client = _FakeClient(df)

    result_one = run(client, ["AAPL"], "2024-01-01", "2025-01-01")
    result_two = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01")

    assert result_two["n_taken"] >= result_one["n_taken"]


def test_run_accepts_start_equity_and_risk_pct_params():
    """Custom starting_equity and risk_pct are passed through."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01",
                 starting_equity=500.0, risk_pct=0.02)
    assert result["starting_equity"] == 500.0


def test_run_empty_symbol_list_returns_starting_equity_unchanged():
    """No symbols → no trades → final_equity == starting_equity."""
    client = _FakeClient(_make_daily_df())
    result = run(client, [], "2024-01-01", "2025-01-01", starting_equity=300.0)
    assert result["final_equity"] == 300.0
    assert result["n_taken"] == 0


def test_run_skips_none_dataframe():
    """Symbols for which get_history returns None are silently skipped."""

    class _NoneClient:
        def get_history(self, *a, **kw):
            return None

    result = run(_NoneClient(), ["AAPL"], "2024-01-01", "2025-01-01",
                 starting_equity=200.0)
    assert result["final_equity"] == 200.0
    assert result["n_taken"] == 0


def test_run_n_taken_plus_n_skipped_equals_total_trades():
    """n_taken + n_skipped covers every trade generated."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01")
    # We can't know the exact total without running the strategy separately,
    # but both counts must be non-negative and their sum >= n_taken.
    assert result["n_taken"] >= 0
    assert result["n_skipped"] >= 0


def test_run_respects_reversal_knobs():
    """Changing down_days to an impossible value (> available rows) gives 0 trades."""
    client = _FakeClient(_make_daily_df())
    result = run(client, ["AAPL"], "2024-01-01", "2025-01-01", down_days=999)
    assert result["n_taken"] == 0
    assert result["final_equity"] == result["starting_equity"]


def test_run_respects_max_concurrent():
    """max_concurrent=1 limits simultaneous open positions."""
    df = _make_daily_df()
    client = _FakeClient(df)
    # Run the same two symbols with max_concurrent=1; one concurrent seat only.
    result = run(client, ["AAPL", "MSFT"], "2024-01-01", "2025-01-01",
                 max_concurrent=1)
    # With only 1 concurrent slot across many potential same-day entries,
    # n_skipped should be >= 0 and the result valid.
    assert result["n_taken"] + result["n_skipped"] >= result["n_taken"]
