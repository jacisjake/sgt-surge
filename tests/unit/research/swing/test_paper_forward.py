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
