from datetime import datetime, time, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.bot.signals.orb import OpeningRangeBreakout
from src.bot.signals.base import SignalDirection


def _bars_df(rows):
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp")


def make_or_bars():
    """Three 5-min bars: 9:30, 9:35, 9:40 ET — total volume 6000, high=10.5, low=9.8."""
    return _bars_df([
        {"timestamp": "2026-05-08T13:30:00Z", "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "volume": 2000},
        {"timestamp": "2026-05-08T13:35:00Z", "open": 10.3, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 2000},
        {"timestamp": "2026-05-08T13:40:00Z", "open": 10.0, "high": 10.45, "low": 9.95, "close": 10.4, "volume": 2000},
    ])


def test_lock_or_records_high_low_and_volume():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    state = strat.state["AAPL"]
    assert state.or_high == 10.5
    assert state.or_low == 9.8
    assert state.or_volume == 6000
    assert state.or_locked is True


def test_breakout_emits_long_signal():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())

    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 2500}
    sig = strat.on_bar(bar)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.entry_price == 10.6
    assert sig.stop_price == 9.8
    assert sig.target_price == pytest.approx(10.6 + 2 * (10.6 - 9.8))
    assert sig.strategy == "orb"


def test_no_signal_when_volume_too_low():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())

    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 100}
    assert strat.on_bar(bar) is None


def test_no_signal_when_close_below_or_high():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.49, "low": 10.4, "close": 10.49, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_does_not_fire_twice():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    bar1 = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
            "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    bar2 = {"symbol": "AAPL", "timestamp": "2026-05-08T13:55:00Z",
            "open": 10.6, "high": 10.8, "low": 10.55, "close": 10.75, "volume": 3000}
    assert strat.on_bar(bar1) is not None
    assert strat.on_bar(bar2) is None


def test_no_signal_when_or_not_locked():
    strat = OpeningRangeBreakout()
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_late_day_cutoff_blocks_entries_after_1515_et():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    # 19:20 UTC = 15:20 ET (DST)
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T19:20:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_reset_clears_all_state():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    strat.reset()
    assert strat.state == {}
