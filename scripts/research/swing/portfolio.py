"""Fractional-share portfolio simulator for dated-trade backtests.

Takes a list of trade dicts (as returned by short_term_reversal_trades) and
simulates a realistic $ equity curve with position sizing, concurrent
position limits, and capital constraints.
"""
from __future__ import annotations

import datetime
from typing import Any


def _max_drawdown_from_curve(
    equity_curve: list[float], starting_equity: float = 0.0
) -> float:
    """Peak-to-trough max drawdown as a fraction of peak equity.

    The peak starts at max(starting_equity, first point) so that a drawdown
    from starting equity is captured even when the curve has only one point.
    """
    if not equity_curve:
        return 0.0
    peak = max(starting_equity, equity_curve[0])
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def simulate_portfolio(
    trades: list[dict],
    starting_equity: float = 200.0,
    risk_pct: float = 0.01,
    max_concurrent: int | None = None,
    min_notional: float = 1.0,
) -> dict:
    """Simulate a fractional-share account over dated trades.

    trades: list of dicts with entry_date, exit_date, return_pct, stop_pct
            (symbol optional). Fractional shares assumed (size by $, not share count).

    Model (realized-equity basis):
      - Sort trades by entry_date (ties: stable).
      - Process entry/exit events in DATE order. Maintain realized_pnl (starts 0),
        available_cash (starts = starting_equity), and a set of open positions.
      - equity = starting_equity + realized_pnl.
      - ENTRY on a trade's entry_date: notional = risk_pct * equity / stop_pct,
        capped at available_cash. If notional < min_notional OR
        (max_concurrent is not None and len(open) >= max_concurrent): SKIP it
        (count as skipped). Else open: available_cash -= notional; remember its
        notional + return_pct + exit_date.
      - EXIT on a trade's exit_date: pnl = notional * return_pct;
        realized_pnl += pnl; available_cash += notional + pnl.
      - After each EXIT, append (equity) to the equity curve.

    Returns dict:
      starting_equity, final_equity, total_return (final/start - 1),
      n_taken, n_skipped, max_drawdown (fraction, computed on the equity-curve
      peaks/troughs), worst_trade_pnl, best_trade_pnl, equity_curve (list of floats).

    NOTE: drawdown is **realized-equity** (marked at exits), so it UNDERSTATES true
    intra-trade drawdown — a known v1 limitation.
    """
    if not trades:
        return {
            "starting_equity": starting_equity,
            "final_equity": starting_equity,
            "total_return": 0.0,
            "n_taken": 0,
            "n_skipped": 0,
            "max_drawdown": 0.0,
            "worst_trade_pnl": 0.0,
            "best_trade_pnl": 0.0,
            "equity_curve": [],
        }

    # Build event list: (date, kind, trade_index, payload)
    # kind: 0 = EXIT (process first), 1 = ENTRY (process second)
    # Each trade contributes an ENTRY event; taken trades also get an EXIT event.
    # We handle EXIT events dynamically as trades are opened.

    # First pass: sort trades by entry_date (stable sort preserves original order
    # for ties — standard Python sort is stable).
    sorted_trades = sorted(trades, key=lambda t: t["entry_date"])

    # We process events chronologically. Because we don't know which trades will
    # be taken until we process entries, we build the event queue dynamically:
    # collect all entry events up front; as entries are taken, push their exit events.

    # Use a simple date-keyed approach: iterate over unique dates in order.
    # Collect all entry events.
    # On each date: first process all exits due that day, then all entries.

    # Map: date -> list of entry trade dicts
    from collections import defaultdict
    entry_events: dict[datetime.date, list[dict]] = defaultdict(list)
    for t in sorted_trades:
        entry_events[t["entry_date"]].append(t)

    # State
    realized_pnl: float = 0.0
    available_cash: float = starting_equity

    # open_positions: list of dicts {exit_date, notional, return_pct}
    open_positions: list[dict[str, Any]] = []

    n_taken: int = 0
    n_skipped: int = 0
    trade_pnls: list[float] = []
    equity_curve: list[float] = []

    # All relevant dates = unique entry dates + exit dates of taken trades
    # We iterate lazily: get all dates we might need to visit.
    all_dates = sorted(set(entry_events.keys()))

    # We also need to visit exit dates that might fall between entry dates.
    # Strategy: maintain a set of pending exit dates, add as trades are opened.
    pending_exit_dates: set[datetime.date] = set()

    date_cursor = 0  # pointer into all_dates

    # Merge entry dates + pending exit dates dynamically.
    # Use a sorted unique set of all dates we need to visit.
    # Rebuild each time? Too expensive. Instead, iterate once collecting
    # all needed dates first (we know all entries; exits are unknown until taken).
    # Simplest correct approach: use a priority-queue of events.

    import heapq

    # event heap: (date, kind, seq, payload)
    # kind: 0 = EXIT, 1 = ENTRY  (ensures exits before entries on same date)
    heap: list[tuple] = []
    seq = 0

    for t in sorted_trades:
        heapq.heappush(heap, (t["entry_date"], 1, seq, t))
        seq += 1

    while heap:
        date, kind, _, payload = heapq.heappop(heap)
        equity = starting_equity + realized_pnl

        if kind == 0:
            # EXIT event
            pos = payload  # {exit_date, notional, return_pct}
            pnl = pos["notional"] * pos["return_pct"]
            realized_pnl += pnl
            available_cash += pos["notional"] + pnl
            trade_pnls.append(pnl)
            open_positions.remove(pos)
            equity_curve.append(starting_equity + realized_pnl)

        else:
            # ENTRY event
            trade = payload
            equity = starting_equity + realized_pnl
            stop_pct = trade.get("stop_pct", 0.05)
            notional = risk_pct * equity / stop_pct
            notional = min(notional, available_cash)

            # Skip conditions
            if notional < min_notional:
                n_skipped += 1
                continue
            if max_concurrent is not None and len(open_positions) >= max_concurrent:
                n_skipped += 1
                continue

            # Open the position
            available_cash -= notional
            pos = {
                "exit_date": trade["exit_date"],
                "notional": notional,
                "return_pct": trade["return_pct"],
            }
            open_positions.append(pos)
            n_taken += 1

            # Schedule the exit
            heapq.heappush(heap, (trade["exit_date"], 0, seq, pos))
            seq += 1

    final_equity = starting_equity + realized_pnl
    total_return = final_equity / starting_equity - 1.0

    best_pnl = max(trade_pnls) if trade_pnls else 0.0
    worst_pnl = min(trade_pnls) if trade_pnls else 0.0

    return {
        "starting_equity": starting_equity,
        "final_equity": final_equity,
        "total_return": total_return,
        "n_taken": n_taken,
        "n_skipped": n_skipped,
        "max_drawdown": _max_drawdown_from_curve(equity_curve, starting_equity),
        "worst_trade_pnl": worst_pnl,
        "best_trade_pnl": best_pnl,
        "equity_curve": equity_curve,
    }
