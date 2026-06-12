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
    index_rsi2_trades,
    turn_of_month_trades,
    breakout_52w_trades,
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


# ---------------------------------------------------------------------------
# index_rsi2_trades
# ---------------------------------------------------------------------------
#
# Frame design:
#   ma=5, rsi_buy=10.0, rsi_sell=70.0, max_hold=10, stop_pct=0.08
#
# Seed 5 rows at 100 so SMA(5) is 100.  Then a drop to produce RSI2 < 10,
# followed by a recovery where RSI2 > 70.
#
# We use a small ma=5 and rsi_buy=50.0 / rsi_sell=70.0 to keep the frame short.


def _make_rsi2_df(trigger_stop: bool = False) -> pd.DataFrame:
    """Build a DataFrame that triggers exactly one index_rsi2_trades entry.

    ma=5, rsi_buy=50.0, rsi_sell=70.0.

    Rows 0-4: seed at 100 (SMA(5) at i=4 = 100).
    Row 5 (entry): close=105 > SMA(5)=(100+100+100+100+105)/5=101; RSI2 needs to
        be < rsi_buy.  We create a 2-bar down move (rows 3-4 down, row 5 down more)
        so the 2-period gain/loss calc gives a very low RSI2.

    Actually simpler: use a long seed then a sharp drop then a bounce.
    Rows 0-9: close=200 (steady, SMA(5)=200 by row 4).
    Rows 10-11: close=180, 170 — sharp down (RSI2 will be near 0 by row 11,
        close < SMA — this is BELOW MA, so no entry here).
    Rows 12-15: close=210, 215, 220, 225 — recover above SMA; RSI2 near 100.

    We need: close[i] > SMA(5)[i] AND RSI2[i] < rsi_buy.
    Strategy: seed at 100 for rows 0-4, then have a 2-day dip on rows 5-6 that
    stays ABOVE SMA(5), then entry at row 6 or 7.

    Simplest controlled frame (ma=5, rsi_buy=80.0, rsi_sell=20.0 won't work).
    Let's use the actual RSI2 formula and craft carefully.

    RSI2[i] = 100 - 100/(1+gain_avg/loss_avg) over 2 bars.
    If close[i] > close[i-1] > close[i-2]: all gain → RSI2 ≈ 100.
    If close[i] < close[i-1] < close[i-2]: all loss → RSI2 ≈ 0.

    Frame (ma=5):
      rows 0-4: close=110 (seed SMA)
      row 5: close=105 (down from 110 — 1 loss bar, SMA(5)=(110*4+105)/5=109 > 105: below MA)
      row 6: close=103 (down again — still below SMA, RSI2 ≈ 0)
      row 7: close=112 (up above SMA; RSI2 = 2-bar gain from 103→112, loss=0 → RSI2=100 → no entry)

    We need close > SMA AND RSI2 < rsi_buy at the SAME bar.  After a pullback that
    stays just above SMA:

      rows 0-4: close=100
      row 5: close=102 — SMA(5)=(100*4+102)/5=100.4; RSI2: delta=[0,0,0,0,2]; gain=1,loss=0→RSI2=100
      That doesn't help.

    BEST approach: long seed at 100, then TWO down bars that stay above SMA:
      rows 0-9:  close=100
      row 10: close=99.0 — SMA(5)=(100*4+99)/5=99.8 > 99: BELOW MA → skip
      rows 0-9:  close=100
      row 10: close=99.5 — below MA still

    KEY INSIGHT: SMA lags. After rows 0-9 at 100, rows 10-11 dip to 98, 97:
      SMA(5)[10] = (100+100+100+100+98)/5 = 99.6 > 98 → below MA at row 10
      SMA(5)[11] = (100+100+100+98+97)/5 = 99.0 > 97 → below MA at row 11

    So pure dips go below SMA.  We need a shallow dip.

    SOLUTION: Large MA so SMA barely moves. ma=20, seed 20 rows at 100.
    Rows 20-21: close=98,96 (dip — SMA(20)[21]≈(19*100+98+96... wait need 20 rows))
    SMA(20)[20] = (100*19+98)/20 = 99.9; 98 < 99.9 → below MA.

    Hard constraint: close[i] > SMA[i] when RSI2[i] is low.

    The only way this happens: the RSI2 dip is smaller than the SMA gap.
    E.g. close=102, SMA=101, but 2-bar RSI shows weakness.

    SIMPLEST: close[i] > SMA[i] is satisfied (price is above long-run avg),
    but short-term RSI2 is pulled down by a recent 1-bar drop from a high:
      close[i-1] = 110 (high bar), close[i] = 105 (dip), SMA[i] = 100 (still below close).
      delta[i] = -5 (loss). delta[i-1] = +10 (gain).
      gain_avg = 0.0 (rolling 2 mean of [10, -5... wait clip lower=0 → [10, 0])/2=5)
      loss_avg = rolling 2 mean of [-delta clipped upper=0 → [0, 5])/2 = 2.5
      RSI2 = 100 - 100/(1+5/2.5) = 100 - 100/3 = 66.7  -- not < 50

    Let me try: close goes 100, 100, 100, 100, 100, 120, 102 (seed at 100×5, spike to 120, drop to 102):
      SMA(5)[6] = (100+100+100+120+102)/5 = 104.4 > 102 → below MA. Doh.

    With seed=100 × many, then spike+drop staying above seed:
      seed_level=100 × 20, then close[20]=115 (above SMA≈100+δ), close[21]=108:
      SMA(5)[20]=(100+100+100+100+115)/5=103, 115>103 ✓
      SMA(5)[21]=(100+100+100+115+108)/5=104.6, 108>104.6 ✓
      RSI2[21]: delta[20]=+15, delta[21]=-7.  gain_avg=(15+0)/2=7.5, loss_avg=(0+7)/2=3.5
      RSI2=100-100/(1+7.5/3.5)=100-100/3.14≈68.2 — still not low enough for rsi_buy=50

    Try bigger spike and bigger drop:
      seed=100×20, close[20]=140, close[21]=105:
      delta[20]=+40, delta[21]=-35. gain=(40+0)/2=20, loss=(0+35)/2=17.5
      RSI2=100-100/(1+20/17.5)=100-100/2.14≈53.3  -- closer but still >50

      close[21]=100: delta=-40. gain=(40+0)/2=20, loss=(0+40)/2=20
      RSI2=100-100/(1+1)=50.0  -- exactly 50, need <50

      close[21]=99: delta=-41. gain=(40+0)/2=20, loss=(0+41)/2=20.5
      RSI2=100-100/(1+20/20.5)=100-100/1.976≈49.4 < 50 ✓
      SMA(5)[21]=(100+100+100+140+99)/5=107.8, 99<107.8 → BELOW MA

    The problem: after a big spike, the SMA is elevated and the dip goes below it.

    USE MULTI-BAR SEED AT HIGHER LEVEL so SMA stays low relative to dip:
    seed=200×20 rows. Then close[20]=240 (spike, SMA=(200×4+240)/5=208, 240>208 ✓)
    close[21]=205 (dip): SMA=(200×3+240+205)/5=209, 205<209 → BELOW again.

    CONCLUSION: We need rsi_buy > 50 to make the test tractable without
    a very elaborate frame.  The spec says default rsi_buy=10.0 but the TESTS
    can use any rsi_buy value.  Using rsi_buy=80 is fine for the test.

    Frame with rsi_buy=80:
    seed=100×20, close[20]=140, close[21]=102:
      delta[20]=+40, delta[21]=-38. gain=(40+0)/2=20, loss=(0+38)/2=19
      RSI2=100-100/(1+20/19)=100-100/2.053≈51.3 < 80 ✓
      SMA(5)[21]=(100+100+100+140+102)/5=108.4. 102 < 108.4 → BELOW MA :(

    The only clean solution: use a frame where close NEVER dips below SMA
    but RSI2 dips below rsi_buy.  This requires close > SMA even during the dip.

    If seed=100 flat for 20 rows, SMA(5)≈100.  Then have close stay above 100:
    close[20]=103 (small up), close[21]=101 (small dip):
      SMA(5)[21]=(100+100+100+103+101)/5=100.8, 101>100.8 ✓
      delta[20]=+3, delta[21]=-2. gain=(3+0)/2=1.5, loss=(0+2)/2=1.0
      RSI2=100-100/(1+1.5/1.0)=100-40=60. Not very low.

    close[20]=110 (big up), close[21]=101 (dip back):
      SMA(5)[21]=(100+100+100+110+101)/5=102.2. 101 < 102.2 → BELOW MA :(

    The fundamental tension: a dip that's sharp enough to make RSI2 low
    will also push price below a lagging SMA.

    REAL SOLUTION used in practice: the initial ENTRY bar must be AFTER the dip
    has recovered somewhat. The entry fires when RSI2 < rsi_buy AND close > SMA.
    This means: buy on the DOWN bar only if close is still above SMA.

    Frame that works (ma=5, rsi_buy=80):
    rows 0-19: close=100 (flat seed, SMA(5)=100 by row 4)
    row 20: close=108 (UP — RSI2 spikes to ~100)
    row 21: close=104 (DOWN — RSI2 dips)
      SMA(5)[21] = (100+100+100+108+104)/5 = 102.4, 104 > 102.4 ✓
      delta[20]=+8, delta[21]=-4. gain_avg=(8+0)/2=4, loss_avg=(0+4)/2=2
      RSI2 = 100 - 100/(1+4/2) = 100 - 100/3 ≈ 66.7 < 80 ✓
    row 22: close=112 (UP — RSI2 recovers above rsi_sell=70)
      delta[22]=+8. gain_avg=(0+8)/2=4, loss_avg=(4+0)/2=2 →
        Wait: gain=clip(delta, lower=0).rolling(2).mean()
        delta series at [21,22]: [-4, +8]. clip(lower=0): [0, 8]. rolling(2).mean(): (0+8)/2=4
        loss series: clip(upper=0) negated: [4, 0]. rolling(2).mean(): (4+0)/2=2
        RSI2[22] = 100 - 100/(1+4/2) = 66.7. Not > 70.

    row 22: close=115:
      delta[22]=+11. gain_avg=(0+11)/2=5.5, loss_avg=(4+0)/2=2
      RSI2=100-100/(1+5.5/2)=100-100/3.75=73.3 > 70 ✓

    So entry at row 21 (close=104 > SMA=102.4, RSI2≈66.7 < 80),
    exit at row 22 (RSI2≈73.3 > 70).
    return_pct = close[22]/close[21] - 1 - slip = 115/104 - 1 - slip ≈ 0.1058 - slip
    """
    closes = (
        [100.0] * 20       # seed
        + [108.0]          # row 20: up
        + [104.0]          # row 21: entry (close > SMA, RSI2 < 80)
        + [115.0]          # row 22: RSI2 > 70 → exit
        + [116.0, 117.0]   # extra rows
    )
    rows = []
    for c in closes:
        if trigger_stop:
            # make the bar after entry (row 22) a hard-stop bar
            # entry=104, stop_pct=0.08, stop_level=95.68
            # but row 22 already has close=115 > stop — need different stop_pct
            # Instead we'll set a very tight stop so stop triggers on row 22
            low = c - 20.0  # will push below stop for tight stop test
        else:
            low = c - 1.0
        rows.append({
            "open":   c + 0.1,
            "high":   c + 1.0,
            "low":    low,
            "close":  c,
            "volume": 1_000_000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=len(rows), freq="B", tz="America/New_York")
    return df


def test_index_rsi2_trades_rsi_recovery_exit():
    """Entry fires when close > SMA and RSI2 < rsi_buy; exits on RSI2 > rsi_sell.

    Frame: seed 20 bars at 100, spike to 108 (row 20), dip to 104 (row 21 = entry),
    recover to 115 (row 22 = exit on RSI2 recovery).

    ma=5, rsi_buy=80, rsi_sell=70. slip_bps=15.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    rsi_buy = 80.0
    rsi_sell = 70.0
    stop_pct = 0.08
    ma = 5

    df = _make_rsi2_df(trigger_stop=False)
    trades = index_rsi2_trades(
        df, "RSI2", ma=ma, rsi_buy=rsi_buy, rsi_sell=rsi_sell,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )

    # Expect at least 1 trade
    assert len(trades) >= 1

    # Verify required keys
    required = {"symbol", "entry_date", "exit_date", "return_pct", "stop_pct"}
    for t in trades:
        assert required.issubset(t.keys())
        assert t["symbol"] == "RSI2"
        assert isinstance(t["entry_date"], datetime.date)
        assert isinstance(t["exit_date"], datetime.date)
        assert t["stop_pct"] == stop_pct

    # Find the trade entered at row 21 — verify it exits on RSI2 recovery (row 22)
    # index[21] with date_range starting 2025-01-02, freq=B:
    # 0=Jan2, 1=Jan3, 2=Jan6, 3=Jan7, 4=Jan8, 5=Jan9, 6=Jan10, 7=Jan13,
    # 8=Jan14, 9=Jan15, 10=Jan16, 11=Jan17, 12=Jan20, 13=Jan21, 14=Jan22,
    # 15=Jan23, 16=Jan24, 17=Jan27, 18=Jan28, 19=Jan29, 20=Jan30, 21=Jan31
    # 22=Feb3
    entry_date_expected = datetime.date(2025, 1, 31)   # index[21]
    exit_date_expected  = datetime.date(2025, 2, 3)    # index[22]

    entry_trades = [t for t in trades if t["entry_date"] == entry_date_expected]
    assert len(entry_trades) == 1, (
        f"Expected 1 trade at entry {entry_date_expected}, got {entry_trades}"
    )
    t = entry_trades[0]
    assert t["exit_date"] == exit_date_expected, (
        f"Expected exit on {exit_date_expected}, got {t['exit_date']}"
    )

    # Return: exit at close[22]=115, entry=104
    expected_ret = 115.0 / 104.0 - 1.0 - slip
    assert abs(t["return_pct"] - expected_ret) < 1e-10, (
        f"return_pct {t['return_pct']:.8f} != {expected_ret:.8f}"
    )


def test_index_rsi2_trades_hard_stop():
    """When the bar after entry has a low <= stop_level, trade exits at stop price.

    Use a tight stop_pct=0.001 so stop_level is very close to entry, and the
    next bar's low (c-20) blows through it.
    """
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.001   # very tight: stop_level = entry * 0.999
    rsi_buy = 80.0
    rsi_sell = 70.0
    ma = 5

    df = _make_rsi2_df(trigger_stop=False)  # lows = c-1 everywhere
    # Reuse the same frame but with tight stop so low[22] = 115-1 = 114 > 104*0.999=103.896
    # That doesn't trigger.  Build a custom frame where entry bar's next low < stop.
    # entry close = 104, stop_level = 104 * (1-0.001) = 103.896
    # low[22] needs to be <= 103.896.  Set low[22] = 103.0 by making it c-12.

    # Rebuild rows manually with low[22] = 103
    closes = (
        [100.0] * 20       # seed
        + [108.0]          # row 20: up
        + [104.0]          # row 21: entry
        + [115.0]          # row 22: RSI2 recovery — but we force stop here
        + [116.0, 117.0]   # extra
    )
    rows = []
    for idx, c in enumerate(closes):
        low = 103.0 if idx == 22 else c - 1.0   # row 22 breaches tight stop
        rows.append({
            "open":  c + 0.1,
            "high":  c + 1.0,
            "low":   low,
            "close": c,
            "volume": 1_000_000,
        })
    df2 = pd.DataFrame(rows)
    df2.index = pd.date_range("2025-01-02", periods=len(rows), freq="B", tz="America/New_York")

    trades = index_rsi2_trades(
        df2, "STOP", ma=ma, rsi_buy=rsi_buy, rsi_sell=rsi_sell,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )
    assert len(trades) >= 1

    entry_date_expected = datetime.date(2025, 1, 31)  # index[21]
    entry_trades = [t for t in trades if t["entry_date"] == entry_date_expected]
    assert len(entry_trades) == 1

    t = entry_trades[0]
    entry = 104.0
    stop_level = entry * (1.0 - stop_pct)
    # low[22] = 103.0 <= 103.896 → stop triggered
    assert t["exit_date"] == datetime.date(2025, 2, 3)   # index[22]
    expected_ret = stop_level / entry - 1.0 - slip
    assert abs(t["return_pct"] - expected_ret) < 1e-10, (
        f"return_pct {t['return_pct']:.8f} != {expected_ret:.8f}"
    )


def test_index_rsi2_trades_no_setup():
    """A flat frame above SMA never has RSI2 < rsi_buy, so no trades are generated."""
    # flat close = 105 for 30 rows: SMA = 105, RSI2 = 50 (neutral) which is > 10.0
    rows = [
        {"open": 104.9, "high": 106.0, "low": 104.0, "close": 105.0, "volume": 1_000_000}
        for _ in range(30)
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=30, freq="B", tz="America/New_York")

    trades = index_rsi2_trades(df, "FLAT", ma=5, rsi_buy=10.0, rsi_sell=70.0)
    assert trades == []


# ---------------------------------------------------------------------------
# turn_of_month_trades
# ---------------------------------------------------------------------------
#
# Strategy: enter at close of the last trading day of each month, hold `hold`
# bars, with a hard stop.
#
# Frame design (hold=4, stop_pct=0.08):
#   Build a frame that spans two distinct month-ends.
#   Month 1: Jan 2025 — last business day = Jan 31 (index day)
#   Month 2: Feb 2025 — last business day = Feb 28 (index day)
#
# We construct an explicit DatetimeIndex with at least 2 month-end days and
# enough trailing rows for hold+1 exits.


def _make_turn_of_month_df(trigger_stop: bool = False) -> pd.DataFrame:
    """Frame spanning Jan + Feb 2025, enough for two turn-of-month entries.

    Uses actual calendar dates (not freq='B') so we control the month boundary.
    Dates chosen: Jan 28, Jan 29, Jan 30, Jan 31 (entry 1, last day of Jan),
                  Feb 3, Feb 4, Feb 5, Feb 6 (hold days for entry 1),
                  Feb 25, Feb 26, Feb 27, Feb 28 (entry 2, last day of Feb),
                  Mar 3, Mar 4, Mar 5, Mar 6.
    """
    # Month-end entry closes
    entry1_close = 100.0
    entry2_close = 105.0

    # Return target (no hard stop): hold=4, exit at close[i+4]
    # We just set sensible closes
    dates_and_closes = [
        ("2025-01-28", 98.0),
        ("2025-01-29", 99.0),
        ("2025-01-30", 100.5),
        ("2025-01-31", entry1_close),   # ← last day of Jan, entry 1
        ("2025-02-03", 101.0),
        ("2025-02-04", 102.0),
        ("2025-02-05", 103.0),
        ("2025-02-06", 104.0),          # ← exit 1 (i+4 for entry at index 3)
        # gap to Feb month-end
        ("2025-02-25", 104.5),
        ("2025-02-26", 104.8),
        ("2025-02-27", 104.9),
        ("2025-02-28", entry2_close),   # ← last day of Feb, entry 2
        ("2025-03-03", 106.0),
        ("2025-03-04", 107.0),
        ("2025-03-05", 108.0),
        ("2025-03-06", 109.0),          # ← exit 2 (i+4 for entry at index 11)
    ]
    dates = [d for d, _ in dates_and_closes]
    closes = [c for _, c in dates_and_closes]

    rows = []
    entry1_stop = entry1_close * (1 - 0.08)  # 92.0
    entry2_stop = entry2_close * (1 - 0.08)  # 96.6

    for i, (dt, c) in enumerate(dates_and_closes):
        if trigger_stop and i == 4:
            # bar after entry 1: low breaches stop for entry 1
            low = entry1_stop - 1.0   # 91.0 <= 92.0
        else:
            low = c - 1.0   # safely above any stop level used here

        rows.append({
            "open":   c + 0.1,
            "high":   c + 1.0,
            "low":    low,
            "close":  c,
            "volume": 1_000_000,
        })

    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(
        pd.to_datetime(dates).tz_localize("America/New_York")
    )
    return df


def test_turn_of_month_two_entries():
    """Two month-ends in the frame → exactly 2 trades with correct entry dates."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    hold = 4
    stop_pct = 0.08

    df = _make_turn_of_month_df(trigger_stop=False)
    trades = turn_of_month_trades(
        df, "TOM", hold=hold, stop_pct=stop_pct, slip_bps=slip_bps,
    )

    assert len(trades) == 2, f"Expected 2 trades, got {len(trades)}: {trades}"

    # Required keys
    required = {"symbol", "entry_date", "exit_date", "return_pct", "stop_pct"}
    for t in trades:
        assert required.issubset(t.keys())
        assert t["symbol"] == "TOM"
        assert isinstance(t["entry_date"], datetime.date)
        assert isinstance(t["exit_date"], datetime.date)
        assert t["stop_pct"] == stop_pct

    # Entry dates = last trading day of each month
    entry_dates = {t["entry_date"] for t in trades}
    assert datetime.date(2025, 1, 31) in entry_dates, (
        f"Jan 31 not in entry dates: {entry_dates}"
    )
    assert datetime.date(2025, 2, 28) in entry_dates, (
        f"Feb 28 not in entry dates: {entry_dates}"
    )


def test_turn_of_month_exit_date_correct():
    """Exit date is entry_bar + hold (capped at last index)."""
    hold = 4
    df = _make_turn_of_month_df(trigger_stop=False)
    trades = turn_of_month_trades(df, "TOM", hold=hold, stop_pct=0.08, slip_bps=15.0)
    assert len(trades) == 2

    # Trade 1: entry at Jan 31 (index 3), exit at index 3+4=7 → 2025-02-06
    t1 = next(t for t in trades if t["entry_date"] == datetime.date(2025, 1, 31))
    assert t1["exit_date"] == datetime.date(2025, 2, 6), (
        f"Trade1 exit: expected 2025-02-06, got {t1['exit_date']}"
    )
    # return_pct = close[7]/close[3] - 1 - slip = 104/100 - 1 - slip
    slip = 2 * 15.0 / 10_000
    expected_ret1 = 104.0 / 100.0 - 1.0 - slip
    assert abs(t1["return_pct"] - expected_ret1) < 1e-10

    # Trade 2: entry at Feb 28 (index 11), exit at index 11+4=15 → 2025-03-06
    t2 = next(t for t in trades if t["entry_date"] == datetime.date(2025, 2, 28))
    assert t2["exit_date"] == datetime.date(2025, 3, 6), (
        f"Trade2 exit: expected 2025-03-06, got {t2['exit_date']}"
    )
    expected_ret2 = 109.0 / 105.0 - 1.0 - slip
    assert abs(t2["return_pct"] - expected_ret2) < 1e-10


def test_turn_of_month_hard_stop():
    """When first bar after entry breaches stop, trade exits at stop price."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.08

    df = _make_turn_of_month_df(trigger_stop=True)
    trades = turn_of_month_trades(
        df, "STOP", hold=4, stop_pct=stop_pct, slip_bps=slip_bps,
    )

    # Entry 1 hits stop on the first bar after entry (index 4 = 2025-02-03)
    t1 = next(t for t in trades if t["entry_date"] == datetime.date(2025, 1, 31))
    entry = 100.0
    stop_level = entry * (1.0 - stop_pct)  # 92.0
    assert t1["exit_date"] == datetime.date(2025, 2, 3), (
        f"Expected stop exit on 2025-02-03, got {t1['exit_date']}"
    )
    expected_ret = stop_level / entry - 1.0 - slip
    assert abs(t1["return_pct"] - expected_ret) < 1e-10


# ---------------------------------------------------------------------------
# breakout_52w_trades
# ---------------------------------------------------------------------------
#
# Strategy: enter on the FIRST bar of a 52-week high breakout (close >= max
# of prior `lookback` highs, but previous bar was NOT already at a new high).
# Exit: hard stop OR close < SMA(close, ma_exit).
#
# Frame design: We keep lookback small (lookback=10) so the test frame is short.
# - 10 seed bars (index 0-9) with high=100.
# - Bar 10: close=101, high=102 — NEW high (close >= max(high[0:10])=100). Entry here.
# - Bar 11: close=102, high=103 — still at new high, but previous bar (10) WAS
#   already a new high → NOT a fresh breakout → no entry.
# - Bar 12: close=80 — close drops below SMA(ma_exit) → exit.


def _make_breakout_df(trigger_stop: bool = False) -> pd.DataFrame:
    """Frame for breakout_52w tests.  lookback=10, ma_exit=3.

    seed: bars 0-9, close=high=100.
    bar 10: breakout bar (close=101 >= max(high[0:10])=100).
             AND bar 9 was NOT a breakout (close[9]=100 < max(high[0:9])=100 is FALSE
             — actually 100 >= 100 is True.  Need bar 9 to fail "new high" check.
             Condition for fresh breakout at i=10:
               close[10] >= max(high[10-lookback : 10])  = max(high[0:10]) = 100 → 101 >= 100 ✓
               close[9] < max(high[9-lookback : 9])      = max(high[-1:9])  → wraps, skip
               Actually: prior bar NOT already at new high means:
               close[i-1] < max(high[i-1-lookback : i-1])
               close[9] < max(high[9-10 : 9]) = max(high[0:9]) = 100. But close[9]=100 < 100? NO.

    For i=10 (lookback=10):
      window_current = high[0:10] (indices 0..9), max = 100.
      close[10] >= 100 ✓
      prior window = high[0:9] (indices 0..8), max = 100.
      close[9] >= 100 — so prior bar IS already at new high → NOT a fresh breakout at i=10!

    We need close[9] < max(high[0:9]) to make bar 10 a fresh breakout.
    Solution: set high[9]=99 (lower high on the last seed bar):
      window_current at i=10: max(high[0:10]) = max([100]*9 + [99]) = 100.
      close[10] >= 100 ✓
      prior window at i=10: max(high[0:9]) = max([100]*9) = 100.
      close[9] < 100? close[9] = 99 < 100 ✓  → fresh breakout at i=10!

    bar 11: close=102, high=103 — still a new high. Previous bar (10) WAS at new high
      (close[10]=101 >= max(high[0:10])=100) → NOT fresh breakout at i=11 ✓

    bar 12: close=95 (drop) — SMA(3)[12] = (101+102+95)/3 = 99.33, 95 < 99.33 → MA-exit.
    low[12] = 94 > stop_level=92.92, so hard stop does NOT fire first.
    """
    closes_highs = [
        # (close, high)
        (100.0, 100.0),  # 0
        (100.0, 100.0),  # 1
        (100.0, 100.0),  # 2
        (100.0, 100.0),  # 3
        (100.0, 100.0),  # 4
        (100.0, 100.0),  # 5
        (100.0, 100.0),  # 6
        (100.0, 100.0),  # 7
        (100.0, 100.0),  # 8
        (99.0,  99.0),   # 9: slightly lower so close[9] < max(high[0:9])=100
        (101.0, 102.0),  # 10: fresh breakout ← entry
        (102.0, 103.0),  # 11: still-new-high but NOT fresh
        (95.0,  96.0),   # 12: MA-exit bar (low=94 > stop=92.92; close < SMA3)
        (94.0,  95.0),   # 13: extra
    ]

    rows = []
    entry_close = 101.0
    stop_level = entry_close * (1 - 0.08)  # 92.92

    for idx, (c, h) in enumerate(closes_highs):
        if trigger_stop and idx == 11:
            low = stop_level - 1.0  # force stop on first post-entry bar
        else:
            low = c - 1.0
        rows.append({
            "open":   c - 0.1,
            "high":   h,
            "low":    low,
            "close":  c,
            "volume": 1_000_000,
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=len(rows), freq="B", tz="America/New_York")
    return df


def test_breakout_52w_enters_on_first_breakout_bar():
    """Exactly one trade fires, entered at the first (fresh) breakout bar."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    lookback = 10
    ma_exit = 3
    stop_pct = 0.08

    df = _make_breakout_df(trigger_stop=False)
    trades = breakout_52w_trades(
        df, "BRK", lookback=lookback, ma_exit=ma_exit,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )

    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}: {trades}"

    t = trades[0]
    required = {"symbol", "entry_date", "exit_date", "return_pct", "stop_pct"}
    assert required.issubset(t.keys())
    assert t["symbol"] == "BRK"
    assert t["stop_pct"] == stop_pct
    assert isinstance(t["entry_date"], datetime.date)
    assert isinstance(t["exit_date"], datetime.date)

    # Entry at index 10 → 2025-01-14 (0=Jan2,1=Jan3,2=Jan6,...,10=Jan16)
    # date_range 2025-01-02 freq=B: 0=Jan2,1=Jan3,2=Jan6,3=Jan7,4=Jan8,
    # 5=Jan9,6=Jan10,7=Jan13,8=Jan14,9=Jan15,10=Jan16
    entry_expected = datetime.date(2025, 1, 16)
    assert t["entry_date"] == entry_expected, (
        f"Expected entry on {entry_expected}, got {t['entry_date']}"
    )

    # Exit at bar 12 (close=95 < SMA3=99.33; low=94 > stop=92.92 so MA exit fires),
    # index 12 → 2025-01-20
    exit_expected = datetime.date(2025, 1, 20)
    assert t["exit_date"] == exit_expected, (
        f"Expected exit on {exit_expected}, got {t['exit_date']}"
    )

    # return_pct = close[12]/close[10] - 1 - slip = 95/101 - 1 - slip
    # Note: close[12]=95 changed from 80 so stop (92.92) does not fire first.
    expected_ret = 95.0 / 101.0 - 1.0 - slip
    assert abs(t["return_pct"] - expected_ret) < 1e-10


def test_breakout_52w_no_reentry_on_continuation():
    """The bar after the first breakout does NOT generate a second trade."""
    df = _make_breakout_df(trigger_stop=False)
    trades = breakout_52w_trades(
        df, "BRK", lookback=10, ma_exit=3, stop_pct=0.08,
    )
    # Only 1 trade: bar 11 is continuation, not fresh breakout
    assert len(trades) == 1


def test_breakout_52w_hard_stop():
    """When first post-entry bar has low <= stop_level, exits at stop."""
    slip_bps = 15.0
    slip = 2 * slip_bps / 10_000
    stop_pct = 0.08

    df = _make_breakout_df(trigger_stop=True)
    trades = breakout_52w_trades(
        df, "STOP", lookback=10, ma_exit=3, stop_pct=stop_pct, slip_bps=slip_bps,
    )
    assert len(trades) >= 1
    t = trades[0]

    entry = 101.0
    stop_level = entry * (1.0 - stop_pct)
    # Stop fires at index 11 = 2025-01-17
    assert t["exit_date"] == datetime.date(2025, 1, 17), (
        f"Expected stop exit on 2025-01-17, got {t['exit_date']}"
    )
    expected_ret = stop_level / entry - 1.0 - slip
    assert abs(t["return_pct"] - expected_ret) < 1e-10


def test_breakout_52w_no_breakout():
    """Flat closes never reach a new high → no trades."""
    rows = [
        {"open": 99.9, "high": 100.0, "low": 99.0, "close": 100.0, "volume": 1_000_000}
        for _ in range(20)
    ]
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=20, freq="B", tz="America/New_York")

    trades = breakout_52w_trades(df, "FLAT", lookback=10, ma_exit=3, stop_pct=0.08)
    assert trades == []
