"""Skew metrics over closed trades — the convex-breakout acceptance report.

Selection is on shape, not P&L: a few trades carrying large R while every loss
stays near 1R. Total return and win rate are deliberately not the headline, and
a book whose best trade never reaches 3R fails regardless of its return line.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

TRAIL_WORKING_R = 3.0


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read closed trades from a journal list or a ledger's ``closed_trades``."""
    p = Path(path)
    if not p.exists() or not p.read_text().strip():
        return []
    data = json.loads(p.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("closed_trades") or [])
    raise ValueError(f"unrecognised trade file: {p}")


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _expectancy(rows: list[dict]) -> Optional[float]:
    return _mean([float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the acceptance metrics. Unknown values are None, never 0.0."""
    scored = [r for r in rows if r.get("r_multiple") is not None]
    rs = [float(r["r_multiple"]) for r in scored]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    mean_win = _mean(wins)
    mean_loss = _mean(losses)
    payoff = (mean_win / abs(mean_loss)) if (mean_win and mean_loss) else None

    gross = sum(wins)
    top3 = sum(sorted(wins, reverse=True)[:3])
    top3_share = (top3 / gross) if gross > 0 else None

    by_regime: dict[str, dict[str, Any]] = {}
    for key, pred in (
        ("risk_on", lambda r: (r.get("regime") or {}).get("risk_on") is True),
        ("risk_off", lambda r: (r.get("regime") or {}).get("risk_on") is False),
        ("unknown", lambda r: not r.get("regime")),
    ):
        bucket = [r for r in rows if pred(r)]
        by_regime[key] = {"n": len(bucket), "expectancy_r": _expectancy(bucket)}

    by_reason: dict[str, int] = {}
    for r in rows:
        reason = str(r.get("reason") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1

    max_winner = max(wins) if wins else None

    return {
        "n_closed": len(rows),
        "n_scored": len(scored),
        "n_unscored": len(rows) - len(scored),
        "win_rate": (len(wins) / len(rs)) if rs else None,
        "expectancy_r": _mean(rs),
        "mean_win_r": mean_win,
        "mean_loss_r": mean_loss,
        "worst_loss_r": min(losses) if losses else None,
        "payoff_ratio": payoff,
        "max_winner_r": max_winner,
        "top3_share": top3_share,
        "by_regime": by_regime,
        "by_reason": dict(sorted(by_reason.items())),
        "trail_working": (
            (max_winner is not None and max_winner >= TRAIL_WORKING_R)
            if rs else None
        ),
    }
