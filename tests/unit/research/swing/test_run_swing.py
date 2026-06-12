"""Tests for swing/run_swing.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.research.swing.run_swing import run


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

def _make_daily_df(n_rows: int = 10) -> pd.DataFrame:
    """Build a plausible daily OHLCV frame with n_rows trading days."""
    closes = [100.0 + i for i in range(n_rows)]
    rows = [
        {
            "open":   c - 0.5,
            "high":   c + 1.0,
            "low":    c - 1.5,
            "close":  c,
            "volume": 1_000_000,
        }
        for c in closes
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        "2025-01-02", periods=n_rows, freq="B", tz="America/New_York"
    )
    return df


class _FakeClient:
    """Fake SchwabClient: always returns the same daily frame for any symbol."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def get_history(self, symbol, freq, start, end, **kwargs) -> pd.DataFrame:
        return self._df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_returns_one_report_per_strategy():
    """run() returns exactly one dict per registered strategy."""
    client = _FakeClient(_make_daily_df(10))
    reports = run(client, ["AAPL"], "2025-01-01", "2025-06-01")
    # Two strategies: overnight_drift and short_term_reversal
    assert len(reports) == 2


def test_run_reports_have_required_keys():
    """Each report dict has all keys produced by metrics.summarize()."""
    client = _FakeClient(_make_daily_df(10))
    reports = run(client, ["AAPL"], "2025-01-01", "2025-06-01")
    required_keys = {"setup", "n", "win_pct", "avg_win", "avg_loss",
                     "expectancy", "profit_factor", "max_drawdown_r",
                     "max_consec_losers"}
    for r in reports:
        assert required_keys.issubset(r.keys()), f"Missing keys in {r}"


def test_run_overnight_drift_has_trades():
    """overnight_drift should record len(df)-1 trades per symbol."""
    n = 10
    client = _FakeClient(_make_daily_df(n))
    reports = run(client, ["AAPL"], "2025-01-01", "2025-06-01")
    od = next(r for r in reports if r["setup"] == "overnight_drift")
    # 1 symbol × (n-1) trades = 9
    assert od["n"] == n - 1


def test_run_with_two_symbols_doubles_overnight_drift_count():
    """Two symbols doubles the overnight_drift trade count."""
    n = 10
    client = _FakeClient(_make_daily_df(n))
    reports = run(client, ["AAPL", "MSFT"], "2025-01-01", "2025-06-01")
    od = next(r for r in reports if r["setup"] == "overnight_drift")
    assert od["n"] == 2 * (n - 1)


def test_run_skips_empty_dataframe():
    """Symbols for which get_history returns an empty frame are silently skipped."""

    class _EmptyClient:
        def get_history(self, symbol, freq, start, end, **kwargs) -> pd.DataFrame:
            return pd.DataFrame()

    reports = run(_EmptyClient(), ["AAPL"], "2025-01-01", "2025-06-01")
    assert len(reports) == 2  # still returns two reports
    for r in reports:
        assert r["n"] == 0


def test_run_sorted_by_expectancy_descending():
    """Reports are sorted by expectancy descending."""
    client = _FakeClient(_make_daily_df(10))
    reports = run(client, ["AAPL"], "2025-01-01", "2025-06-01")
    expectancies = [r["expectancy"] for r in reports]
    assert expectancies == sorted(expectancies, reverse=True)
