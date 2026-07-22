"""Scoreboard + staleness metrics."""
from __future__ import annotations

from datetime import date, timedelta

from src.lab.ledger import new_state
from src.lab.metrics.daily_equity import (
    is_ledger_stale,
    scoreboard,
)


def test_scoreboard_rolling_and_gap():
    s = new_state(200.0)
    s["realized_pnl"] = 10.0
    s["last_date"] = "2026-07-01"
    s["equity_curve_daily"] = [
        {"date": "2026-06-01", "equity_realized": 200.0, "daily_return": 0.0},
        {"date": "2026-06-02", "equity_realized": 202.0, "daily_return": 0.01},
        {"date": "2026-06-03", "equity_realized": 204.0, "daily_return": 0.01},
    ]
    board = scoreboard(s, north_star=0.01, rolling_window=2)
    assert board["equity_realized"] == 210.0
    assert abs(board["rolling_mean_daily_return"] - 0.01) < 1e-9
    assert abs(board["distance_to_goal"]) < 1e-9


def test_stale_when_last_date_old():
    s = new_state(200.0)
    s["last_date"] = (date.today() - timedelta(days=14)).isoformat()
    stale, detail = is_ledger_stale(s, as_of=date.today(), max_sessions=3)
    assert stale is True
    assert detail["weekdays_since"] > 3


def test_not_stale_when_last_date_recent():
    s = new_state(200.0)
    s["last_date"] = date.today().isoformat()
    stale, detail = is_ledger_stale(s, as_of=date.today(), max_sessions=3)
    assert stale is False
