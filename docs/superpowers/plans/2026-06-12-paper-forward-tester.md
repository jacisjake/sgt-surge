# Paper Forward Tester (Breakout 52W) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateful daily paper-trading forward-tester for the 52-week-high breakout strategy that persists a ledger to JSON, processes one trading day at a time, and is fully idempotent on re-runs.

**Architecture:** A single new module `scripts/research/swing/paper_forward.py` holds all state management (new/load/save), the core `step()` function that processes exits then entries for one day, and `run_once()` which fetches bars from Schwab and advances the ledger. Tests live in `tests/unit/research/swing/test_paper_forward.py` and use synthetic DataFrames — no live API calls.

**Tech Stack:** Python 3.12, pandas, json, datetime, argparse; existing `SchwabClient.get_history`, existing `breakout_52w_trades` entry/exit semantics.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/research/swing/paper_forward.py` | Create | All forward-tester logic |
| `tests/unit/research/swing/test_paper_forward.py` | Create | All unit tests |

---

### Task 1: `new_state`, `load_state`, `save_state` + round-trip test

**Files:**
- Create: `scripts/research/swing/paper_forward.py`
- Create: `tests/unit/research/swing/test_paper_forward.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/research/swing/test_paper_forward.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -v 2>&1 | head -30`

Expected: `ModuleNotFoundError` or `ImportError` — `paper_forward` does not exist yet.

- [ ] **Step 3: Write minimal implementation for Task 1**

```python
# scripts/research/swing/paper_forward.py
"""Stateful daily paper forward-tester for the 52-week-high breakout strategy.

Simulates fills only — NEVER places a real order.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# State schema helpers
# ---------------------------------------------------------------------------

def new_state(starting_equity: float = 200.0) -> dict:
    """Return a blank ledger dict."""
    return {
        "starting_equity": starting_equity,
        "available_cash": starting_equity,
        "realized_pnl": 0.0,
        "last_date": None,
        "open_positions": [],
        "closed_trades": [],
    }


def load_state(path: str) -> dict:
    """Load ledger from *path*; return new_state() if the file is missing."""
    p = Path(path)
    if not p.exists():
        return new_state()
    with p.open() as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    """Write ledger to *path* as pretty-printed JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py::test_new_state_default_equity tests/unit/research/swing/test_paper_forward.py::test_new_state_custom_equity tests/unit/research/swing/test_paper_forward.py::test_save_load_roundtrip tests/unit/research/swing/test_paper_forward.py::test_load_state_missing_file_returns_new_state tests/unit/research/swing/test_paper_forward.py::test_save_state_pretty_json -v`

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/research/swing/paper_forward.py tests/unit/research/swing/test_paper_forward.py
git commit -m "feat: add paper_forward state helpers (new/load/save) with tests"
```

---

### Task 2: `is_fresh_breakout`

**Files:**
- Modify: `scripts/research/swing/paper_forward.py` (add function)
- Modify: `tests/unit/research/swing/test_paper_forward.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/research/swing/test_paper_forward.py`:

```python
# ---------------------------------------------------------------------------
# is_fresh_breakout
# ---------------------------------------------------------------------------

def _make_breakout_arrays(lookback: int = 10):
    """Return (highs, closes, fresh_i, stale_i) for is_fresh_breakout tests.

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "is_fresh_breakout" -v 2>&1 | head -20`

Expected: `ImportError` — `is_fresh_breakout` not defined yet.

- [ ] **Step 3: Implement `is_fresh_breakout`**

Add after `save_state` in `paper_forward.py`:

```python
# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def is_fresh_breakout(
    highs: list[float],
    closes: list[float],
    i: int,
    lookback: int,
) -> bool:
    """True iff bar i is the FIRST bar of a new lookback-bar high.

    Conditions:
      1. closes[i] >= max(highs[i-lookback : i])     — current bar at new high
      2. closes[i-1] < max(highs[prev_start : i-1])  — prior bar was NOT at new high

    Guard: prev_start = max(0, i - 1 - lookback).
    """
    # Current bar must clear the lookback-bar high window
    window_cur_max = max(highs[i - lookback: i])
    if closes[i] < window_cur_max:
        return False

    # Prior bar must NOT have been at a new high
    prev_start = max(0, i - 1 - lookback)
    window_prev_max = max(highs[prev_start: i - 1])
    return closes[i - 1] < window_prev_max
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "is_fresh_breakout" -v`

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/research/swing/paper_forward.py tests/unit/research/swing/test_paper_forward.py
git commit -m "feat: add is_fresh_breakout with guard + 4 tests"
```

---

### Task 3: `step` — open positions on fresh breakout

**Files:**
- Modify: `scripts/research/swing/paper_forward.py`
- Modify: `tests/unit/research/swing/test_paper_forward.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/research/swing/test_paper_forward.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "step_opens or step_does_not_dup" -v 2>&1 | head -20`

Expected: `ImportError` — `step` not defined yet.

- [ ] **Step 3: Implement `step` (entries only for now)**

Add after `is_fresh_breakout` in `paper_forward.py`:

```python
# ---------------------------------------------------------------------------
# Core step function
# ---------------------------------------------------------------------------

def step(
    state: dict,
    bars_by_symbol: dict[str, pd.DataFrame],
    today: datetime.date,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> dict:
    """Advance the paper ledger by one trading day.

    Parameters
    ----------
    state          : ledger dict (mutated in-place and returned)
    bars_by_symbol : symbol -> daily DataFrame (cols open/high/low/close,
                     DatetimeIndex), including today's row and >= lookback prior rows
    today          : the trading date to process
    risk_pct       : fraction of equity risked per trade
    lookback       : bars in the 52-week-high lookback window
    ma_exit        : SMA period for the trend-break exit
    stop_pct       : hard-stop distance from entry as a fraction
    slip_bps       : one-way slippage in basis points (applied twice per round-trip)

    Returns the (mutated) state dict.
    """
    # --- IDEMPOTENCY ----------------------------------------------------------
    if state["last_date"] is not None:
        if today <= datetime.date.fromisoformat(state["last_date"]):
            return state

    slip = 2 * slip_bps / 10_000
    equity = state["starting_equity"] + state["realized_pnl"]

    # Helper: find the positional index for today in a DataFrame
    def _today_idx(df: pd.DataFrame) -> int | None:
        dates = df.index.normalize().date
        matches = [i for i, d in enumerate(dates) if d == today]
        return matches[0] if matches else None

    # --- EXITS FIRST ----------------------------------------------------------
    remaining_positions = []
    for pos in state["open_positions"]:
        sym = pos["symbol"]
        df = bars_by_symbol.get(sym)
        if df is None or df.empty:
            remaining_positions.append(pos)
            continue

        i = _today_idx(df)
        if i is None:
            remaining_positions.append(pos)
            continue

        closes = df["close"].to_numpy()
        lows = df["low"].to_numpy()
        sma_exit_series = df["close"].rolling(ma_exit).mean()
        sma_exit_today = sma_exit_series.iloc[i]

        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        notional = pos["notional"]

        exit_price = None
        reason = None

        if lows[i] <= stop_price:
            exit_price = stop_price
            reason = "stop"
        elif (not pd.isna(sma_exit_today)) and (closes[i] < sma_exit_today):
            exit_price = closes[i]
            reason = "trend_break"

        if exit_price is not None:
            # pnl = notional * ((exit_price*(1-slip)) / (entry_price*(1+slip)) - 1)
            pnl = notional * ((exit_price * (1 - slip)) / (entry_price * (1 + slip)) - 1)
            state["realized_pnl"] += pnl
            state["available_cash"] += notional + pnl
            equity = state["starting_equity"] + state["realized_pnl"]
            state["closed_trades"].append({
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": today.isoformat(),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
                "reason": reason,
            })
        else:
            remaining_positions.append(pos)

    state["open_positions"] = remaining_positions

    # --- ENTRIES --------------------------------------------------------------
    open_symbols = {p["symbol"] for p in state["open_positions"]}

    for sym, df in bars_by_symbol.items():
        if sym in open_symbols:
            continue
        if df is None or df.empty:
            continue

        i = _today_idx(df)
        if i is None or i < lookback:
            continue

        highs = df["high"].to_numpy()
        closes = df["close"].to_numpy()

        if not is_fresh_breakout(highs, closes, i, lookback):
            continue

        entry_price = closes[i]
        notional = min(risk_pct * equity / stop_pct, state["available_cash"])
        if notional < 1.0:
            continue

        stop_price = entry_price * (1 - stop_pct)
        state["open_positions"].append({
            "symbol": sym,
            "entry_date": today.isoformat(),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "notional": notional,
        })
        state["available_cash"] -= notional
        open_symbols.add(sym)

    state["last_date"] = today.isoformat()
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "step_opens or step_does_not_dup" -v`

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/research/swing/paper_forward.py tests/unit/research/swing/test_paper_forward.py
git commit -m "feat: add step() with entries; tests for open position and dedup guard"
```

---

### Task 4: `step` — exits (stop and trend-break) + idempotency

**Files:**
- Modify: `tests/unit/research/swing/test_paper_forward.py`

All `step` implementation is already done. These tests exercise the exit paths and idempotency.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/research/swing/test_paper_forward.py`:

```python
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
    # low_today=84.0 > stop_price=92.0 → stop does NOT fire first.
    df = _make_exit_df(close_today=85.0, low_today=84.0, ma_exit=ma_exit, seed_close=100.0)
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
```

- [ ] **Step 2: Run tests to verify they fail (or pass — step is already implemented)**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "exit or stop or trend_break or idempotent" -v`

Expected: some tests may already pass since `step` is implemented; verify all pass.

- [ ] **Step 3: No implementation changes needed — step already handles exits and idempotency**

If any tests fail, inspect the failure message and fix the implementation or the expected value. The most likely issue is the `_today_idx` helper using `.date` (a numpy array method) — verify it works on tz-aware DatetimeIndex:

```python
# In step(), _today_idx uses:
dates = df.index.normalize().date   # returns numpy array of date objects
```

If that fails replace with:
```python
dates = [ts.date() for ts in df.index]
```

- [ ] **Step 4: Confirm all tests pass**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -v`

Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/unit/research/swing/test_paper_forward.py scripts/research/swing/paper_forward.py
git commit -m "feat: add step() exit tests (stop, trend_break) + idempotency tests"
```

---

### Task 5: sizing edge cases (tiny equity, cap at available_cash)

**Files:**
- Modify: `tests/unit/research/swing/test_paper_forward.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/research/swing/test_paper_forward.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they pass (implementation already handles this)**

Run: `python -m pytest tests/unit/research/swing/test_paper_forward.py -k "notional_capped or skips_entry" -v`

Expected: 2 PASSED

- [ ] **Step 3: Commit**

```bash
git add tests/unit/research/swing/test_paper_forward.py
git commit -m "test: add sizing edge-case tests (cap at cash, skip below min notional)"
```

---

### Task 6: `run_once` and `main`

**Files:**
- Modify: `scripts/research/swing/paper_forward.py`

No new tests for `run_once`/`main` — they wrap API calls and are covered by integration. The unit tests already validate all the logic inside `step`.

- [ ] **Step 1: Append `run_once` and `main` to `paper_forward.py`**

```python
# ---------------------------------------------------------------------------
# run_once — fetch bars and advance state
# ---------------------------------------------------------------------------

def run_once(
    client,
    symbols: list[str],
    state_path: str,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> dict:
    """Fetch daily bars and step the paper ledger forward by one day.

    Parameters
    ----------
    client     : SchwabClient with get_history(symbol, timeframe, start, end)
    symbols    : list of ticker strings to process
    state_path : path to the JSON ledger file
    Others     : forwarded directly to step()

    Returns the updated state dict.
    """
    import datetime as _dt
    today_wall = _dt.date.today()
    # Fetch bars starting ~2 years ago (generous window for lookback+warmup)
    fetch_start = today_wall - _dt.timedelta(days=(lookback + 30) * 2)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, today_wall)
        if df is not None and not df.empty:
            bars_by_symbol[sym] = df

    if not bars_by_symbol:
        print("No bars fetched — nothing to process.")
        state = load_state(state_path)
        return state

    # today = last bar date across all symbols (the most-recent shared trading day)
    today = max(df.index[-1].date() for df in bars_by_symbol.values())

    state = load_state(state_path)
    prev_open = len(state["open_positions"])
    prev_closed = len(state["closed_trades"])

    state = step(
        state, bars_by_symbol, today,
        risk_pct=risk_pct, lookback=lookback, ma_exit=ma_exit,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )
    save_state(state_path, state)

    # Print summary
    equity = state["starting_equity"] + state["realized_pnl"]
    new_opens = state["open_positions"][prev_open:]
    new_closes = state["closed_trades"][prev_closed:]
    print(f"\n=== Paper Forward — {today} ===")
    print(f"  Equity          : ${equity:.2f}")
    print(f"  Open positions  : {len(state['open_positions'])}")
    if new_opens:
        print("  NEW ENTRIES:")
        for pos in new_opens:
            print(f"    {pos['symbol']}  entry={pos['entry_price']:.2f}  "
                  f"stop={pos['stop_price']:.2f}  notional=${pos['notional']:.2f}")
    if new_closes:
        print("  EXITS:")
        for t in new_closes:
            print(f"    {t['symbol']}  exit={t['exit_price']:.2f}  "
                  f"pnl=${t['pnl']:.2f}  reason={t['reason']}")
    print()
    return state


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """CLI for the paper forward-tester."""
    p = argparse.ArgumentParser(
        description="Daily paper forward-tester for 52-week-high breakout strategy."
    )
    p.add_argument("--symbols-file", required=True,
                   help="Path to whitespace-delimited ticker file")
    p.add_argument("--state-file", default="state/swing_paper_breakout.json",
                   help="Path to JSON ledger file (default: state/swing_paper_breakout.json)")
    p.add_argument("--risk-pct", type=float, default=0.01,
                   help="Fraction of equity risked per trade (default 0.01)")
    p.add_argument("--lookback", type=int, default=252,
                   help="Lookback bars for 52-week-high window (default 252)")
    p.add_argument("--ma-exit", type=int, default=50,
                   help="SMA period for trend-break exit (default 50)")
    p.add_argument("--stop-pct", type=float, default=0.08,
                   help="Hard stop distance from entry as fraction (default 0.08)")
    p.add_argument("--slip-bps", type=float, default=15.0,
                   help="One-way slippage in bps (default 15)")
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [
        s.strip().upper()
        for s in Path(args.symbols_file).read_text().split()
        if s.strip()
    ]
    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
    )
    run_once(
        client=client,
        symbols=symbols,
        state_path=args.state_file,
        risk_pct=args.risk_pct,
        lookback=args.lookback,
        ma_exit=args.ma_exit,
        stop_pct=args.stop_pct,
        slip_bps=args.slip_bps,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the full test suite to verify nothing broke**

Run: `python -m pytest tests/unit/research/ -v`

Expected: all previously-passing tests still pass, and all new paper_forward tests pass.

- [ ] **Step 3: Commit**

```bash
git add scripts/research/swing/paper_forward.py
git commit -m "feat: add run_once() + main() CLI to paper_forward"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run all research unit tests**

Run: `python -m pytest tests/unit/research/ -v`

Expected: all tests PASSED (96 existing + new paper_forward tests).

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "from scripts.research.swing.paper_forward import new_state, load_state, save_state, is_fresh_breakout, step, run_once, main; print('OK')" 2>&1`

Expected: `OK`

---

## Self-Review

### Spec coverage

| Spec requirement | Covered in task |
|-----------------|-----------------|
| `new_state(starting_equity)` | Task 1 |
| `load_state(path)` — returns new_state() if missing | Task 1 |
| `save_state(path, state)` — pretty JSON | Task 1 |
| `is_fresh_breakout(highs, closes, i, lookback)` | Task 2 |
| `step` — idempotency guard | Task 4 |
| `step` — exits first (stop + trend_break) | Task 4 |
| `step` — entries (fresh breakout, skip duplicate symbol) | Task 3 |
| `step` — notional sizing, cap at available_cash, skip < 1.0 | Task 5 |
| `run_once` — fetch bars, step, save, print summary | Task 6 |
| `main` — argparse, SchwabClient construction | Task 6 |
| `equity = starting_equity + realized_pnl` | Task 3 (step implementation) |
| `slip = 2*slip_bps/10000` applied to PnL | Task 4 (stop test) |
| `pnl = notional*((exit*(1-slip))/(entry*(1+slip)) - 1)` | Task 4 |
| run_once uses `today = max last-bar date across symbols` | Task 6 |
| state_path default `"state/swing_paper_breakout.json"` | Task 6 |

### No placeholders: checked — all steps have concrete code.

### Type consistency: `step` returns `dict`; `is_fresh_breakout` returns `bool`; both consistent across all tasks.
