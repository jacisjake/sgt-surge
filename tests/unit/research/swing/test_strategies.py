"""Tests for swing/strategies.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from scripts.research.swing.strategies import (
    overnight_drift,
    short_term_reversal,
    short_term_reversal_trades,
    trend_pullback_trades,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal daily OHLCV DataFrame with a DatetimeIndex."""
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=len(df), freq="B", tz="America/New_York")
    return df


# ---------------------------------------------------------------------------
# overnight_drift
# ---------------------------------------------------------------------------

def test_overnight_drift_returns_correct_fractional_returns():
    """Buy close[i], sell open[i+1]; return = open[i+1]/close[i] - 1 - 2*slip."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000  # 0.003

    df = _make_df([
        {"open": 100.0, "high": 105.0, "low": 99.0,  "close": 102.0, "volume": 1_000_000},
        {"open": 104.0, "high": 107.0, "low": 101.0, "close": 105.0, "volume": 1_200_000},
        {"open": 103.0, "high": 108.0, "low": 102.0, "close": 106.0, "volume": 1_100_000},
    ])

    returns = overnight_drift(df, slip_bps=slip_bps)

    # trade 0: open[1]/close[0] - 1 - slip = 104/102 - 1 - 0.003
    expected_0 = 104.0 / 102.0 - 1.0 - slip
    # trade 1: open[2]/close[1] - 1 - slip = 103/105 - 1 - 0.003
    expected_1 = 103.0 / 105.0 - 1.0 - slip

    assert len(returns) == 2  # len(df) - 1
    assert abs(returns[0] - expected_0) < 1e-10
    assert abs(returns[1] - expected_1) < 1e-10


def test_overnight_drift_length_is_n_minus_one():
    """Result length equals number of rows minus 1."""
    df = _make_df([
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 500_000},
        {"open": 10.5, "high": 11.0, "low": 10.0, "close": 11.0, "volume": 600_000},
        {"open": 11.0, "high": 11.5, "low": 10.5, "close": 11.2, "volume": 700_000},
        {"open": 11.2, "high": 12.0, "low": 11.0, "close": 11.8, "volume": 800_000},
    ])
    returns = overnight_drift(df)
    assert len(returns) == 3


def test_overnight_drift_single_row_returns_empty():
    """One-row frame has no overnight trade."""
    df = _make_df([{"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 500_000}])
    assert overnight_drift(df) == []


# ---------------------------------------------------------------------------
# short_term_reversal
# ---------------------------------------------------------------------------

def test_short_term_reversal_hits_target():
    """Entry when close > SMA(5) and 3 consecutive down closes; exit on target.

    Frame design (ma=5, down_days=3):
    - rows 0-1: low seed closes (50) to anchor SMA below the declining sequence
    - rows 2-4: strictly decreasing 120→115→110→105 (down_days=3 checks i-3..i)
    - row 5 (i=4): entry day; close=105, SMA(5)=(50+50+120+115+105)/5=88 < 105 ✓
      closes[4]=105 < closes[3]=115 < closes[2]=120  (but we need [i-3]>[i-2]>[i-1]>[i])
      Actually need i=4 as entry: check closes[4-k] < closes[4-k-1] for k=0,1,2:
        closes[4]=105 < closes[3]=115 ✓; closes[3]=115 < closes[2]=120 ✓; closes[2]=120 < closes[1]? NO.
    For down_days=3 at i=4: need closes[4]<closes[3], closes[3]<closes[2], closes[2]<closes[1].
    Use: row0=50, row1=130, row2=125, row3=120, row4=105 (entry).
      SMA(5) at i=4 = (50+130+125+120+105)/5 = 106.0 > 105? Let's pick row4=108:
      SMA=(50+130+125+120+108)/5=106.6 < 108 ✓
      dec: 108<120<125<130 ✓
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    target_pct = 0.10
    stop_pct = 0.05
    down_days = 3
    hold = 5
    ma = 5

    # entry day i=4: close=108, SMA(5)=106.6, target=108*1.10=118.8
    entry_price = 108.0
    target = entry_price * (1 + target_pct)  # 118.8

    rows = [
        # seed: low close to pull SMA down
        {"open": 50.0, "high": 52.0, "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        # strictly decreasing from here to entry
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        # entry day i=4: close=108 > SMA(5)=(50+130+125+120+108)/5=106.6; dec: 120>...>108 ✓
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": entry_price, "volume": 1_000_000},
        # day i+1=5: high >= target (118.8) -> exit at target
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        # extra rows
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    returns = short_term_reversal(
        df, down_days=down_days, hold=hold, stop_pct=stop_pct,
        target_pct=target_pct, ma=ma, slip_bps=slip_bps,
    )

    # Should have at least one trade (entry at day 4)
    assert len(returns) >= 1
    # The trade entered at day 4 exits at target on day 5
    expected_return = target_pct - slip  # target/entry - 1 - slip
    # Find the trade closest to expected
    closest = min(returns, key=lambda r: abs(r - expected_return))
    assert abs(closest - expected_return) < 1e-10


def test_short_term_reversal_no_qualifying_setup_returns_empty():
    """Monotonically rising closes never satisfy the down_days condition."""
    df = _make_df([
        {"open": 100.0, "high": 102.0, "low": 99.0,  "close": 101.0, "volume": 1_000_000},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1_000_000},
        {"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 1_000_000},
        {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 1_000_000},
        {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
    ])
    returns = short_term_reversal(df, down_days=3, ma=3)
    assert returns == []


def test_short_term_reversal_stop_exit():
    """Entry hits the stop on next bar; return = -stop_pct - slip.

    Same frame design as target test but the bounce bar hits the stop instead.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.05
    target_pct = 0.10
    ma = 5

    entry_price = 108.0
    stop_level = entry_price * (1 - stop_pct)  # 102.6

    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        # entry day i=4: same as target test
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": entry_price, "volume": 1_000_000},
        # next bar: low <= stop_level (101.0 < 102.6) -> stopped out; high not reaching target
        {"open": 107.0, "high": 108.0, "low": 101.0, "close": 105.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    returns = short_term_reversal(
        df, down_days=3, hold=5, stop_pct=stop_pct,
        target_pct=target_pct, ma=ma, slip_bps=slip_bps,
    )

    assert len(returns) >= 1
    expected_return = -stop_pct - slip  # stop_level/entry - 1 - slip
    closest = min(returns, key=lambda r: abs(r - expected_return))
    assert abs(closest - expected_return) < 1e-10


# ---------------------------------------------------------------------------
# short_term_reversal_trades
# ---------------------------------------------------------------------------

def test_short_term_reversal_trades_returns_dicts_with_required_keys():
    """Each element returned must be a dict with the required keys."""
    slip_bps = 15.0
    stop_pct = 0.05
    target_pct = 0.10
    ma = 5

    entry_price = 108.0

    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": entry_price, "volume": 1_000_000},
        # high >= target (118.8) -> exit at target
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    trades = short_term_reversal_trades(
        df, symbol="TEST", down_days=3, hold=5, stop_pct=stop_pct,
        target_pct=target_pct, ma=ma, slip_bps=slip_bps,
    )

    assert len(trades) >= 1
    required_keys = {"symbol", "entry_date", "exit_date", "return_pct", "stop_pct"}
    for t in trades:
        assert required_keys.issubset(t.keys()), f"Missing keys: {t}"


def test_short_term_reversal_trades_symbol_propagated():
    """The symbol field in each dict matches the argument passed in."""
    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": 108.0, "volume": 1_000_000},
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    trades = short_term_reversal_trades(df, symbol="AAPL", ma=5)
    for t in trades:
        assert t["symbol"] == "AAPL"


def test_short_term_reversal_trades_return_pct_matches_bare_version():
    """return_pct in dicts must exactly match the bare-float version.

    Same 10-row frame used in test_short_term_reversal_hits_target.
    The target-hit trade should produce the same return_pct as short_term_reversal.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.05
    target_pct = 0.10
    ma = 5

    entry_price = 108.0
    target = entry_price * (1 + target_pct)  # 118.8

    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": entry_price, "volume": 1_000_000},
        # high >= target on next bar -> target exit
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    bare_returns = short_term_reversal(
        df, down_days=3, hold=5, stop_pct=stop_pct,
        target_pct=target_pct, ma=ma, slip_bps=slip_bps,
    )
    trades = short_term_reversal_trades(
        df, symbol="TEST", down_days=3, hold=5, stop_pct=stop_pct,
        target_pct=target_pct, ma=ma, slip_bps=slip_bps,
    )

    # Same number of trades
    assert len(trades) == len(bare_returns)

    # return_pcts match bare returns in order
    for t, r in zip(trades, bare_returns):
        assert abs(t["return_pct"] - r) < 1e-10, (
            f"return_pct mismatch: {t['return_pct']} vs {r}"
        )


def test_short_term_reversal_trades_dates_are_date_objects():
    """entry_date and exit_date must be datetime.date instances."""
    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": 108.0, "volume": 1_000_000},
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    trades = short_term_reversal_trades(df, symbol="TEST", ma=5)
    assert len(trades) >= 1
    for t in trades:
        assert isinstance(t["entry_date"], datetime.date)
        assert isinstance(t["exit_date"], datetime.date)
        assert t["exit_date"] >= t["entry_date"]


def test_short_term_reversal_trades_entry_and_exit_dates_correct():
    """entry_date=df.index[i].date(), exit_date=df.index[exit_j].date().

    With the 10-row frame: entry at index 4 (2025-01-08 Business day),
    exit at index 5 (target hit on next bar = 2025-01-09 Business day).
    _make_df uses pd.date_range('2025-01-02', freq='B'), so:
      index[0]=2025-01-02, [1]=2025-01-03, [2]=2025-01-06, [3]=2025-01-07,
      [4]=2025-01-08 (entry), [5]=2025-01-09 (exit).
    """
    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        # entry day i=4 -> 2025-01-08
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": 108.0, "volume": 1_000_000},
        # exit day j=5 -> 2025-01-09 (high >= target)
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    trades = short_term_reversal_trades(
        df, symbol="TEST", down_days=3, hold=5, stop_pct=0.05,
        target_pct=0.10, ma=5, slip_bps=15.0,
    )

    # Find the trade with entry on 2025-01-08
    entry_dt = datetime.date(2025, 1, 8)
    exit_dt = datetime.date(2025, 1, 9)
    matching = [t for t in trades if t["entry_date"] == entry_dt]
    assert len(matching) == 1, f"Expected 1 trade at entry_date={entry_dt}, got {matching}"
    assert matching[0]["exit_date"] == exit_dt


def test_short_term_reversal_trades_stop_pct_field():
    """stop_pct in dict matches the argument passed."""
    rows = [
        {"open": 50.0,  "high": 52.0,  "low": 48.0,  "close": 50.0,  "volume": 1_000_000},
        {"open": 131.0, "high": 132.0, "low": 129.0, "close": 130.0, "volume": 1_000_000},
        {"open": 126.0, "high": 127.0, "low": 124.0, "close": 125.0, "volume": 1_000_000},
        {"open": 121.0, "high": 122.0, "low": 119.0, "close": 120.0, "volume": 1_000_000},
        {"open": 108.5, "high": 110.0, "low": 107.0, "close": 108.0, "volume": 1_000_000},
        {"open": 109.0, "high": 120.0, "low": 108.0, "close": 119.0, "volume": 1_200_000},
        {"open": 118.0, "high": 122.0, "low": 117.0, "close": 120.0, "volume": 1_000_000},
        {"open": 119.0, "high": 123.0, "low": 118.0, "close": 121.0, "volume": 1_000_000},
        {"open": 120.0, "high": 124.0, "low": 119.0, "close": 122.0, "volume": 1_000_000},
        {"open": 121.0, "high": 125.0, "low": 120.0, "close": 123.0, "volume": 1_000_000},
    ]
    df = _make_df(rows)

    for sp in (0.03, 0.07):
        trades = short_term_reversal_trades(df, symbol="TEST", stop_pct=sp, ma=5)
        for t in trades:
            assert t["stop_pct"] == sp


def test_short_term_reversal_trades_empty_when_no_setups():
    """Returns [] when no qualifying setups exist."""
    df = _make_df([
        {"open": 100.0, "high": 102.0, "low": 99.0,  "close": 101.0, "volume": 1_000_000},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1_000_000},
        {"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 1_000_000},
        {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 1_000_000},
        {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 1_000_000},
        {"open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0, "volume": 1_000_000},
    ])
    trades = short_term_reversal_trades(df, symbol="TEST", down_days=3, ma=3)
    assert trades == []


# ---------------------------------------------------------------------------
# trend_pullback_trades
# ---------------------------------------------------------------------------
#
# Frame design (ma_entry=5, ma_exit=2, down_days=3, stop_pct=0.08):
#
# 12 rows with closes:
#   [0]=100, [1]=110, [2]=120, [3]=130, [4]=140, [5]=139, [6]=138, [7]=137,
#   [8]=138, [9]=139, [10]=140, [11]=130
#
# Entry at i=7:
#   - SMA(5)[7] = (130+140+139+138+137)/5 = 136.8 < 137 (uptrend ✓)
#   - closes[7]=137 < closes[6]=138 < closes[5]=139 < closes[4]=140 (3 down ✓)
#   - stop_level = 137 * (1-0.08) = 126.04
#
# Lows set to close - 3 for post-entry rows (all > 126.04, no stop trigger).
# Exit at j=11: close=130 < SMA(2)[11]=135 → MA-break exit.
#
# Expected return = 130/137 - 1 - 2*15/10000 ≈ -0.0541

def _make_trend_df(stop_triggered: bool = False) -> pd.DataFrame:
    """12-row frame for trend_pullback tests.

    When stop_triggered=True the bar after entry has a low <= stop level,
    so the trade exits at the hard stop.  Otherwise lows are safely above.
    """
    closes = [100.0, 110.0, 120.0, 130.0, 140.0, 139.0, 138.0, 137.0,
              138.0, 139.0, 140.0, 130.0]

    rows = []
    stop_level = 137.0 * (1 - 0.08)  # 126.04

    for idx_i, c in enumerate(closes):
        if stop_triggered and idx_i == 8:
            # bar right after entry: low breaches the stop
            low = stop_level - 1.0   # 125.04 ≤ 126.04
        else:
            low = c - 3.0            # safely above 126.04 for post-entry bars

        rows.append({
            "open":   c + 0.1,
            "high":   c + 1.0,
            "low":    low,
            "close":  c,
            "volume": 1_000_000,
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        "2025-01-02", periods=len(df), freq="B", tz="America/New_York"
    )
    return df


def test_trend_pullback_trades_ma_break_exit():
    """One trade fired, exits on MA(2) break, exit_date and return correct.

    Entry i=7 (2025-01-13). Exit j=11 (2025-01-17).
    Expected return ≈ 130/137 - 1 - 2*15/10000.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.08
    ma_entry = 5
    ma_exit = 2
    down_days = 3

    df = _make_trend_df(stop_triggered=False)
    trades = trend_pullback_trades(
        df, "TPB", down_days=down_days,
        ma_entry=ma_entry, ma_exit=ma_exit,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )

    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}: {trades}"
    t = trades[0]

    # Required keys present
    assert {"symbol", "entry_date", "exit_date", "return_pct", "stop_pct"}.issubset(t)

    # Symbol propagated
    assert t["symbol"] == "TPB"

    # Dates
    assert t["entry_date"] == datetime.date(2025, 1, 13)   # index[7]
    assert t["exit_date"]  == datetime.date(2025, 1, 17)   # index[11]

    # Return: exit at close[11]=130, entry=137
    expected_ret = 130.0 / 137.0 - 1.0 - slip
    assert abs(t["return_pct"] - expected_ret) < 1e-10, (
        f"return_pct {t['return_pct']:.8f} != expected {expected_ret:.8f}"
    )

    # stop_pct stored correctly
    assert t["stop_pct"] == stop_pct

    # Return is positive-leaning but in this test it's actually negative
    # (130<137 = dip exit). The important thing is it fired exactly once.


def test_trend_pullback_trades_hard_stop_exit():
    """Trade exits at hard stop when bar after entry breaches stop level.

    Entry at 137, stop_pct=0.08 → stop_level=126.04.
    Bar i+1 has low=125.04 ≤ 126.04 → exit at stop_level.
    Expected return ≈ -stop_pct - 2*slip.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.08

    df = _make_trend_df(stop_triggered=True)
    trades = trend_pullback_trades(
        df, "TPB_STOP", down_days=3,
        ma_entry=5, ma_exit=2,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )

    assert len(trades) >= 1
    # Find the trade that entered at 137 (entry_date=2025-01-13)
    t = next(x for x in trades if x["entry_date"] == datetime.date(2025, 1, 13))

    entry = 137.0
    stop_level = entry * (1 - stop_pct)
    expected_ret = stop_level / entry - 1.0 - slip   # ≈ -0.083
    assert abs(t["return_pct"] - expected_ret) < 1e-10, (
        f"return_pct {t['return_pct']:.8f} != expected {expected_ret:.8f}"
    )

    # Exit is on the first bar after entry (index[8] = 2025-01-14)
    assert t["exit_date"] == datetime.date(2025, 1, 14)


def test_trend_pullback_trades_no_setup_returns_empty():
    """Monotonically rising closes never produce a qualifying 3-down sequence."""
    rows = [
        {"open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": float(c), "volume": 1_000_000}
        for c in range(100, 115)
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=len(rows), freq="B", tz="America/New_York")

    trades = trend_pullback_trades(df, "NONE", down_days=3, ma_entry=5, ma_exit=2)
    assert trades == []
