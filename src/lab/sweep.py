"""k1×k2 parameter sweep with skew-based selection.

Selection deliberately ignores total return. Ranking on return is what produced
the +55% breakout_52w backtest that failed forward, because it rewards a high
win rate with capped upside — the opposite of this strategy's payoff shape.

A pair is selectable only when all four hold:

  1. it produced a right tail          (max winner ≥ 3R)
  2. expectancy in R is positive
  3. the sample is not thin            (≥ min_trades closed)
  4. its grid neighbours are also sane (not an isolated overfit spike)

If nothing qualifies, that is a finding to report — not a cue to relax the bar.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.lab.metrics.journal_report import TRAIL_WORKING_R, summarize

MIN_TRADES_DEFAULT = 15


def sweep_grid(
    k1_values: list[float],
    k2_values: list[float],
    *,
    base_params: dict[str, Any],
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run *runner* once per (k1, k2) and summarize each cell's closed trades.

    A cell that raises is recorded with its error rather than aborting the
    sweep — one bad window should not cost the whole grid.
    """
    cells: list[dict[str, Any]] = []
    for k1 in k1_values:
        for k2 in k2_values:
            params = {**base_params, "k1": k1, "k2": k2}
            cell: dict[str, Any] = {"k1": k1, "k2": k2}
            try:
                result = runner(params)
            except Exception as e:  # noqa: BLE001
                cell["error"] = f"{type(e).__name__}: {e}"
                cell["summary"] = summarize([])
                cell["metrics"] = {}
            else:
                closed = (result.get("state") or {}).get("closed_trades") or []
                cell["summary"] = summarize(closed)
                cell["metrics"] = result.get("metrics") or {}
            cells.append(cell)
    return cells


def _qualifies(cell: dict, min_trades: int) -> Optional[str]:
    """Return None when the cell is selectable, else the disqualifying reason."""
    s = cell.get("summary") or {}
    if cell.get("error"):
        return "error"
    if int(s.get("n_closed") or 0) < min_trades:
        return "insufficient_trades"
    if not s.get("trail_working"):
        return "no_right_tail"
    exp = s.get("expectancy_r")
    if exp is None or exp <= 0:
        return "no_positive_expectancy"
    return None


def _is_sane(cell: dict) -> bool:
    """A neighbour is sane if it did not lose money over the window."""
    s = cell.get("summary") or {}
    exp = s.get("expectancy_r")
    return (not cell.get("error")) and exp is not None and exp > 0


def _neighbours(cells: list[dict], k1: float, k2: float) -> list[dict]:
    k1s = sorted({c["k1"] for c in cells})
    k2s = sorted({c["k2"] for c in cells})
    i, j = k1s.index(k1), k2s.index(k2)
    wanted = set()
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ni, nj = i + di, j + dj
        if 0 <= ni < len(k1s) and 0 <= nj < len(k2s):
            wanted.add((k1s[ni], k2s[nj]))
    return [c for c in cells if (c["k1"], c["k2"]) in wanted]


def _rank_key(cell: dict) -> tuple:
    s = cell["summary"]
    return (
        float(s.get("payoff_ratio") or 0.0),
        float(s.get("max_winner_r") or 0.0),
        float(s.get("top3_share") or 0.0),
    )


def select_pair(
    cells: list[dict[str, Any]],
    *,
    min_trades: int = MIN_TRADES_DEFAULT,
) -> dict[str, Any]:
    """Pick a (k1, k2) on skew, or explain why none qualified."""
    surface = sorted(cells, key=lambda c: (c["k1"], c["k2"]))
    out: dict[str, Any] = {"selected": None, "surface": surface}

    if not cells:
        out["reason"] = "empty_grid"
        out["finding"] = "The grid is empty — nothing was run."
        return out

    reasons = {}
    qualified = []
    for c in cells:
        why = _qualifies(c, min_trades)
        if why is None:
            qualified.append(c)
        else:
            reasons[(c["k1"], c["k2"])] = why

    if not qualified:
        # Report the most informative failure across the whole grid.
        order = ["no_right_tail", "no_positive_expectancy",
                 "insufficient_trades", "error"]
        present = [r for r in order if r in reasons.values()]
        reason = present[0] if present else "no_right_tail"
        out["reason"] = reason
        out["finding"] = {
            "no_right_tail": (
                f"No parameter pair produced a winner of {TRAIL_WORKING_R:.0f}R or more. "
                "The trail is not generating a right tail on this universe — report "
                "this and stop, rather than shipping a tuned number."
            ),
            "no_positive_expectancy": (
                "No parameter pair produced positive expectancy in R."
            ),
            "insufficient_trades": (
                f"No parameter pair reached {min_trades} closed trades — the window "
                "or universe is too small to decide anything."
            ),
            "error": "Every cell failed to run; see the surface for details.",
        }[reason]
        return out

    stable = [c for c in qualified
              if all(_is_sane(n) for n in _neighbours(cells, c["k1"], c["k2"]))]

    if not stable:
        best = max(qualified, key=_rank_key)
        out["reason"] = "unstable_neighbourhood"
        out["finding"] = (
            f"k1={best['k1']} k2={best['k2']} passed on its own but its neighbouring "
            "cells did not. An isolated good cell is overfit, not an edge."
        )
        return out

    winner = max(stable, key=_rank_key)
    out["selected"] = {"k1": winner["k1"], "k2": winner["k2"]}
    out["selected_summary"] = winner["summary"]
    out["reason"] = "selected"
    return out
