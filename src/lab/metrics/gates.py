"""Soft promotion metrics read from a validated backtest report."""
from __future__ import annotations

from typing import Any


def evaluate_soft_gates(report: dict, gates: dict[str, Any]) -> dict[str, Any]:
    """Return check result with pass/fail per soft gate.

    *report* is a backtest report as written by
    ``src.lab.runners.backtest.write_report_from_backtest`` — its ``metrics``
    block carries ``n_taken``, ``max_drawdown`` and ``expectancy``.
    """
    min_trades = int(gates.get("min_closed_trades", 15))
    max_dd = float(gates.get("max_drawdown", 0.20))
    require_pos = bool(gates.get("require_positive_expectancy", True))

    metrics = report.get("metrics") or {}
    n_taken = int(metrics.get("n_taken", 0) or 0)
    dd = float(metrics.get("max_drawdown", 0.0) or 0.0)
    exp = metrics.get("expectancy")
    exp = float(exp) if exp is not None else None

    checks = {
        "min_closed_trades": {
            "required": min_trades,
            "actual": n_taken,
            "ok": n_taken >= min_trades,
        },
        "max_drawdown": {
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
