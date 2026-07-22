"""Daily equity scoreboard vs 1% north-star + ledger staleness."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from src.lab.ledger import load_state, realized_equity
from src.lab.metrics.gates import expectancy_per_trade, max_drawdown_realized
from src.lab.registry import Experiment, resolve_ledger_path


NORTH_STAR_DAILY = 0.01


def rolling_mean_daily_return(state: dict, window: int = 20) -> Optional[float]:
    curve = state.get("equity_curve_daily") or []
    rets = [float(r.get("daily_return", 0.0) or 0.0) for r in curve if "daily_return" in r]
    if not rets:
        return None
    tail = rets[-window:]
    return sum(tail) / len(tail)


def scoreboard(
    state: dict,
    *,
    north_star: float = NORTH_STAR_DAILY,
    rolling_window: int = 20,
) -> dict[str, Any]:
    """North-star tracking metrics from a paper ledger."""
    start = float(state.get("starting_equity", 0.0) or 0.0)
    eq = realized_equity(state)
    total_return = (eq / start - 1.0) if start else 0.0
    roll = rolling_mean_daily_return(state, rolling_window)
    gap = None if roll is None else north_star - roll
    exp = expectancy_per_trade(state)
    return {
        "starting_equity": start,
        "equity_realized": eq,
        "total_return": total_return,
        "realized_pnl": float(state.get("realized_pnl", 0.0) or 0.0),
        "n_open": len(state.get("open_positions") or []),
        "n_closed": len(state.get("closed_trades") or []),
        "last_date": state.get("last_date"),
        "max_drawdown": max_drawdown_realized(state),
        "expectancy_per_trade": exp,
        "rolling_mean_daily_return": roll,
        "rolling_window": rolling_window,
        "north_star_daily_return": north_star,
        "distance_to_goal": gap,
        "note": "north_star is a measurement target only, not a promised edge",
    }


def scoreboard_for_experiment(exp: Experiment, *, ledger_path: Optional[str] = None) -> dict[str, Any]:
    path = ledger_path or resolve_ledger_path(exp)
    state = load_state(path, starting_equity=exp.capital)
    out = scoreboard(state)
    out["experiment_id"] = exp.id
    out["strategy"] = exp.strategy
    out["ledger_path"] = path
    return out


def is_ledger_stale(
    state: dict,
    *,
    as_of: Optional[date] = None,
    max_sessions: int = 3,
) -> tuple[bool, dict[str, Any]]:
    """True if last_date is older than *max_sessions* calendar weekdays (approx trading days).

    Uses business-day approximation (weekdays only) without a full market calendar
    dependency — good enough for the 3-session silence alert.
    """
    as_of = as_of or date.today()
    last = state.get("last_date")
    if not last:
        return True, {
            "stale": True,
            "reason": "no last_date",
            "last_date": None,
            "as_of": as_of.isoformat(),
            "max_sessions": max_sessions,
        }
    last_d = date.fromisoformat(last)
    # Count weekdays strictly after last_d up to as_of
    sessions = 0
    d = last_d + timedelta(days=1)
    while d <= as_of:
        if d.weekday() < 5:
            sessions += 1
        d += timedelta(days=1)
    stale = sessions > max_sessions
    return stale, {
        "stale": stale,
        "last_date": last,
        "as_of": as_of.isoformat(),
        "weekdays_since": sessions,
        "max_sessions": max_sessions,
        "reason": "ok" if not stale else f"{sessions} weekdays since last_date (limit {max_sessions})",
    }


def check_experiment_staleness(
    exp: Experiment,
    *,
    as_of: Optional[date] = None,
    max_sessions: int = 3,
    ledger_path: Optional[str] = None,
) -> dict[str, Any]:
    path = ledger_path or resolve_ledger_path(exp)
    state = load_state(path, starting_equity=exp.capital)
    stale, detail = is_ledger_stale(state, as_of=as_of, max_sessions=max_sessions)
    detail["experiment_id"] = exp.id
    detail["ledger_path"] = path
    return detail
