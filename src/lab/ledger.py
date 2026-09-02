"""Experiment ledger helpers and PositionView adapters."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.lab.protocol import PositionView, PortfolioView


def new_state(starting_equity: float = 200.0) -> dict:
    """Blank experiment ledger."""
    return {
        "starting_equity": starting_equity,
        "available_cash": starting_equity,
        "realized_pnl": 0.0,
        "last_date": None,
        "open_positions": [],
        "closed_trades": [],
        "equity_curve_daily": [],
    }


def load_state(path: str, starting_equity: float = 200.0) -> dict:
    p = Path(path)
    if not p.exists():
        return new_state(starting_equity=starting_equity)
    with p.open() as f:
        state = json.load(f)
    state.setdefault("equity_curve_daily", [])
    return state


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def realized_equity(state: dict) -> float:
    return float(state["starting_equity"]) + float(state["realized_pnl"])


def positions_to_views(open_positions: list[dict]) -> list[PositionView]:
    views: list[PositionView] = []
    for pos in open_positions:
        entry = float(pos["entry_price"])
        notional = float(pos["notional"])
        qty = notional / entry if entry > 0 else 0.0
        views.append(
            PositionView(
                symbol=pos["symbol"],
                qty=qty,
                avg_entry_price=entry,
                entry_date=date.fromisoformat(pos["entry_date"]),
                stop_price=pos.get("stop_price"),
                notional=notional,
                metadata=pos.get("metadata") or {},
            )
        )
    return views


def portfolio_from_state(state: dict, as_of: date) -> PortfolioView:
    return PortfolioView(
        as_of=as_of,
        equity=realized_equity(state),
        available_cash=float(state["available_cash"]),
        positions=positions_to_views(state.get("open_positions") or []),
    )


def append_equity_snapshot(state: dict, as_of: date) -> None:
    """Append realized equity snapshot for *as_of* (replace same-date row)."""
    eq = realized_equity(state)
    curve: list[dict[str, Any]] = state.setdefault("equity_curve_daily", [])
    # drop same date if re-run after dual-run tests mutate
    curve[:] = [r for r in curve if r.get("date") != as_of.isoformat()]
    prev_eq = curve[-1]["equity_realized"] if curve else float(state["starting_equity"])
    daily_return = (eq / prev_eq - 1.0) if prev_eq else 0.0
    curve.append(
        {
            "date": as_of.isoformat(),
            "equity_realized": eq,
            "daily_return": daily_return,
            "open_positions": len(state.get("open_positions") or []),
            "cash": float(state["available_cash"]),
        }
    )
