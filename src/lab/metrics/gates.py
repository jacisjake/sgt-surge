"""Soft promotion metrics from paper ledgers (realized equity only)."""
from __future__ import annotations

from typing import Any


def max_drawdown_realized(state: dict) -> float:
    """Peak-to-trough drawdown on realized equity path (0..1)."""
    start = float(state.get("starting_equity", 0.0))
    curve = state.get("equity_curve_daily") or []
    if curve:
        equities = [float(r["equity_realized"]) for r in curve]
    else:
        # Fall back: starting + cumulative closed trade pnl path
        equities = [start]
        eq = start
        for t in state.get("closed_trades") or []:
            eq += float(t.get("pnl", 0.0))
            equities.append(eq)
    peak = equities[0] if equities else start
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    return max_dd


def expectancy_per_trade(state: dict) -> float | None:
    trades = state.get("closed_trades") or []
    if not trades:
        return None
    return sum(float(t.get("pnl", 0.0)) for t in trades) / len(trades)


def paper_trading_days(state: dict) -> int:
    """Count distinct sessions advanced (equity curve or last_date proxy)."""
    curve = state.get("equity_curve_daily") or []
    if curve:
        return len(curve)
    if state.get("last_date"):
        return 1
    return 0


def evaluate_soft_gates(state: dict, gates: dict[str, Any]) -> dict[str, Any]:
    """Return check result with pass/fail per soft gate."""
    min_days = int(gates.get("min_paper_trading_days", 40))
    min_trades = int(gates.get("min_closed_trades", 15))
    max_dd = float(gates.get("max_paper_drawdown", 0.20))
    require_pos = bool(gates.get("require_positive_expectancy", True))

    n_days = paper_trading_days(state)
    n_closed = len(state.get("closed_trades") or [])
    dd = max_drawdown_realized(state)
    exp = expectancy_per_trade(state)

    checks = {
        "min_paper_trading_days": {
            "required": min_days,
            "actual": n_days,
            "ok": n_days >= min_days,
        },
        "min_closed_trades": {
            "required": min_trades,
            "actual": n_closed,
            "ok": n_closed >= min_trades,
        },
        "max_paper_drawdown": {
            "required": max_dd,
            "actual": dd,
            "ok": dd <= max_dd,
        },
        "require_positive_expectancy": {
            "required": True if require_pos else None,
            "actual": exp,
            "ok": (not require_pos) or (exp is not None and exp > 0),
        },
    }
    return {
        "passed": all(c["ok"] for c in checks.values()),
        "checks": checks,
    }
