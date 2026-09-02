"""Plan on the last COMPLETE daily bar, not a partial one.

Schwab returns a bar for the current session as soon as it opens, with `close`
holding the live price. Planning on that evaluates a 52-week-high breakout
against a bar that is hours from finished. The runner was scheduled at 16:05 to
dodge this — which meant orders queued blind and filled at the next open.
Resolving the session explicitly lets it run during market hours on yesterday's
completed bar instead.
"""
from __future__ import annotations

import datetime

import pandas as pd

from scripts.live_swing import resolve_session

ET = "America/New_York"


def _bars(dates):
    idx = pd.to_datetime(dates).tz_localize(ET)
    return {"AAA": pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
                                index=idx)}


def test_drops_todays_partial_bar_while_the_market_is_open():
    bars = _bars(["2026-09-01", "2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 10, 39)          # 10:39 ET, mid-session
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 1)


def test_uses_todays_bar_once_the_session_has_closed():
    bars = _bars(["2026-09-01", "2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 16, 5)           # after the close
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 2)


def test_uses_todays_bar_exactly_at_the_close():
    bars = _bars(["2026-09-01", "2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 16, 0)
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 2)


def test_premarket_falls_back_to_the_prior_session():
    bars = _bars(["2026-09-01", "2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 8, 15)           # pre-market
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 1)


def test_no_partial_bar_present_uses_the_latest_bar():
    """Before the session's bar appears at all, the latest bar is complete."""
    bars = _bars(["2026-08-31", "2026-09-01"])
    now = datetime.datetime(2026, 9, 2, 10, 39)
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 1)


def test_explicit_as_of_wins():
    bars = _bars(["2026-09-01", "2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 16, 5)
    assert resolve_session(bars, now_et=now,
                           as_of=datetime.date(2026, 8, 31)) == datetime.date(2026, 8, 31)


def test_single_partial_bar_leaves_nothing_to_plan_on():
    """Refuse rather than plan on a partial bar with no prior session."""
    bars = _bars(["2026-09-02"])
    now = datetime.datetime(2026, 9, 2, 10, 39)
    assert resolve_session(bars, now_et=now) is None


def test_uses_the_latest_complete_date_across_ragged_symbols():
    idx_a = pd.to_datetime(["2026-08-31", "2026-09-01", "2026-09-02"]).tz_localize(ET)
    idx_b = pd.to_datetime(["2026-08-31", "2026-09-01"]).tz_localize(ET)
    f = lambda i: pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=i)
    bars = {"AAA": f(idx_a), "BBB": f(idx_b)}
    now = datetime.datetime(2026, 9, 2, 10, 39)
    assert resolve_session(bars, now_et=now) == datetime.date(2026, 9, 1)
