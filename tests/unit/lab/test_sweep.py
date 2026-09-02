"""k1×k2 grid sweep and skew-based selection.

The spec is explicit that selection must not be on total return — that rule is
what produced the +55% breakout_52w claim that failed forward. These tests pin
the alternative: a pair is only selectable if it produced a right tail, has
positive expectancy, and sits in a neighbourhood of sane cells.
"""
from __future__ import annotations

import pytest

from src.lab.sweep import select_pair, sweep_grid


def _cell(k1, k2, *, max_r=5.0, payoff=3.0, expectancy=0.5, top3=0.8, n=20):
    return {
        "k1": k1,
        "k2": k2,
        "metrics": {"total_return": 0.5},
        "summary": {
            "n_closed": n,
            "max_winner_r": max_r,
            "payoff_ratio": payoff,
            "expectancy_r": expectancy,
            "top3_share": top3,
            "trail_working": (max_r is not None and max_r >= 3.0),
        },
    }


def _sane_grid(over=None):
    """3x3 grid where every cell is acceptable unless overridden."""
    over = over or {}
    cells = []
    for k1 in (1.5, 2.0, 2.5):
        for k2 in (2.5, 3.0, 4.0):
            cells.append(_cell(k1, k2, **over.get((k1, k2), {})))
    return cells


# ── selection ──────────────────────────────────────────────────────────────

def test_selects_the_best_payoff_among_qualifying_cells():
    cells = _sane_grid({(2.0, 3.0): {"payoff": 6.0}})
    result = select_pair(cells)
    assert result["selected"] == {"k1": 2.0, "k2": 3.0}
    assert result["reason"] == "selected"


def test_rejects_a_cell_with_no_right_tail_even_if_profitable():
    """A capped winner is a failure by the objective, not a lesser success."""
    cells = [_cell(2.0, 3.0, max_r=2.4, payoff=99.0, expectancy=5.0)]
    result = select_pair(cells)
    assert result["selected"] is None
    assert result["reason"] == "no_right_tail"


def test_no_right_tail_anywhere_is_reported_as_a_finding():
    cells = [_cell(k1, 3.0, max_r=1.9) for k1 in (1.5, 2.0, 2.5, 3.0)]
    result = select_pair(cells)
    assert result["selected"] is None
    assert result["reason"] == "no_right_tail"
    assert "no parameter pair produced" in result["finding"].lower()


def test_rejects_negative_expectancy():
    cells = [_cell(2.0, 3.0, expectancy=-0.2)]
    result = select_pair(cells)
    assert result["selected"] is None
    assert result["reason"] == "no_positive_expectancy"


def test_rejects_a_lone_spike_surrounded_by_bad_neighbours():
    """An isolated good cell is overfit, not an edge."""
    cells = []
    for k1 in (1.5, 2.0, 2.5):
        for k2 in (2.5, 3.0, 4.0):
            if (k1, k2) == (2.0, 3.0):
                cells.append(_cell(k1, k2, payoff=9.0, expectancy=2.0))
            else:
                cells.append(_cell(k1, k2, expectancy=-0.5, max_r=1.0))
    result = select_pair(cells)
    assert result["selected"] is None
    assert result["reason"] == "unstable_neighbourhood"


def test_prefers_a_stable_cell_over_a_higher_but_isolated_one():
    cells = _sane_grid()
    # isolate a high-payoff corner by poisoning everything around it
    for c in cells:
        if (c["k1"], c["k2"]) == (1.5, 2.5):
            c["summary"]["payoff_ratio"] = 20.0
        elif c["k1"] == 1.5 or c["k2"] == 2.5:
            c["summary"]["expectancy_r"] = -1.0
            c["summary"]["max_winner_r"] = 0.5
            c["summary"]["trail_working"] = False
    result = select_pair(cells)
    assert result["selected"] != {"k1": 1.5, "k2": 2.5}
    assert result["reason"] == "selected"


def test_thin_samples_are_not_selectable():
    cells = [_cell(2.0, 3.0, n=3)]
    result = select_pair(cells, min_trades=15)
    assert result["selected"] is None
    assert result["reason"] == "insufficient_trades"


def test_empty_grid_selects_nothing():
    result = select_pair([])
    assert result["selected"] is None
    assert result["reason"] == "empty_grid"


def test_surface_is_returned_sorted_for_inspection():
    """The whole grid is reported — the winner is never shown without it."""
    result = select_pair(_sane_grid())
    assert len(result["surface"]) == 9
    keys = [(c["k1"], c["k2"]) for c in result["surface"]]
    assert keys == sorted(keys)


# ── grid execution ─────────────────────────────────────────────────────────

def test_sweep_grid_runs_every_combination_once():
    seen = []

    def fake_runner(params):
        seen.append((params["k1"], params["k2"]))
        return {"state": {"closed_trades": []}, "metrics": {"total_return": 0.0}}

    cells = sweep_grid([1.5, 2.0], [2.5, 3.0, 4.0], base_params={"lookback": 252},
                       runner=fake_runner)
    assert len(cells) == 6
    assert sorted(seen) == [(1.5, 2.5), (1.5, 3.0), (1.5, 4.0),
                            (2.0, 2.5), (2.0, 3.0), (2.0, 4.0)]


def test_sweep_grid_summarizes_each_cell_from_its_closed_trades():
    def fake_runner(params):
        return {
            "state": {"closed_trades": [
                {"r_multiple": 5.0, "reason": "trail"},
                {"r_multiple": -1.0, "reason": "stop"},
            ]},
            "metrics": {"total_return": 0.12},
        }

    cells = sweep_grid([2.0], [3.0], base_params={}, runner=fake_runner)
    assert cells[0]["summary"]["max_winner_r"] == pytest.approx(5.0)
    assert cells[0]["summary"]["expectancy_r"] == pytest.approx(2.0)
    assert cells[0]["metrics"]["total_return"] == pytest.approx(0.12)


def test_sweep_grid_keeps_base_params_and_overrides_only_k():
    captured = {}

    def fake_runner(params):
        captured.update(params)
        return {"state": {"closed_trades": []}, "metrics": {}}

    sweep_grid([2.5], [4.0], base_params={"lookback": 252, "k1": 99, "k2": 99},
               runner=fake_runner)
    assert captured["k1"] == 2.5
    assert captured["k2"] == 4.0
    assert captured["lookback"] == 252


def test_sweep_grid_records_a_failed_cell_without_aborting_the_sweep():
    def fake_runner(params):
        if params["k1"] == 2.0:
            raise ValueError("no bars for this window")
        return {"state": {"closed_trades": []}, "metrics": {}}

    cells = sweep_grid([1.5, 2.0], [3.0], base_params={}, runner=fake_runner)
    assert len(cells) == 2
    failed = [c for c in cells if c.get("error")]
    assert len(failed) == 1
    assert "no bars" in failed[0]["error"]
