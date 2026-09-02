"""Skew metrics over the closed-trade journal.

The convex-breakout design is accepted on shape, not P&L: a few trades carrying
large R while every loss stays near 1R. These tests pin the arithmetic that
decides that, including the rule that a book whose best trade never reaches 3R
has a trail that is not working — whatever the return line says.
"""
from __future__ import annotations

import json

import pytest

from src.lab.metrics.journal_report import load_rows, summarize


def _t(r, *, reason="stop", risk_on=None, symbol="AAA"):
    row = {"symbol": symbol, "r_multiple": r, "reason": reason}
    if risk_on is not None:
        row["regime"] = {"risk_on": risk_on, "spy_vs_sma200": 0.05}
    return row


# ── empty / degenerate ─────────────────────────────────────────────────────

def test_empty_journal_reports_nothing_rather_than_zero():
    s = summarize([])
    assert s["n_closed"] == 0
    assert s["expectancy_r"] is None
    assert s["payoff_ratio"] is None
    assert s["max_winner_r"] is None
    assert s["trail_working"] is None


def test_all_losses_has_no_payoff_ratio():
    s = summarize([_t(-1.0), _t(-1.0)])
    assert s["win_rate"] == 0.0
    assert s["expectancy_r"] == -1.0
    assert s["payoff_ratio"] is None       # no winners to divide by
    assert s["max_winner_r"] is None
    assert s["trail_working"] is False


# ── core skew arithmetic ───────────────────────────────────────────────────

def test_core_metrics_on_a_known_set():
    # R = 5, -1, -1, 2, -1  → sum 4 over 5 trades
    s = summarize([_t(5.0), _t(-1.0), _t(-1.0), _t(2.0), _t(-1.0)])
    assert s["n_closed"] == 5
    assert s["n_scored"] == 5
    assert s["win_rate"] == pytest.approx(0.4)
    assert s["expectancy_r"] == pytest.approx(0.8)
    assert s["mean_win_r"] == pytest.approx(3.5)
    assert s["mean_loss_r"] == pytest.approx(-1.0)
    assert s["payoff_ratio"] == pytest.approx(3.5)
    assert s["max_winner_r"] == pytest.approx(5.0)


def test_top3_share_of_gross_gain():
    # winners 10, 5, 3, 1, 1 → gross 20, top three 18
    s = summarize([_t(10.0), _t(5.0), _t(3.0), _t(1.0), _t(1.0)])
    assert s["top3_share"] == pytest.approx(0.9)


def test_top3_share_is_one_when_three_or_fewer_winners():
    s = summarize([_t(5.0), _t(2.0), _t(-1.0)])
    assert s["top3_share"] == pytest.approx(1.0)


def test_losses_clustering_near_one_r_is_reported():
    """A correctly scaled stop puts every loss near −1R."""
    s = summarize([_t(-0.98), _t(-1.02), _t(-1.0), _t(4.0)])
    assert s["mean_loss_r"] == pytest.approx(-1.0)
    assert s["worst_loss_r"] == pytest.approx(-1.02)


# ── the acceptance rule ────────────────────────────────────────────────────

def test_trail_is_working_when_a_trade_exceeds_3r():
    assert summarize([_t(3.1), _t(-1.0)])["trail_working"] is True


def test_trail_is_not_working_when_the_best_trade_is_small():
    """Positive expectancy is not enough — a capped winner fails the objective."""
    s = summarize([_t(2.0), _t(2.0), _t(-1.0)])
    assert s["expectancy_r"] > 0
    assert s["trail_working"] is False


# ── unscored trades ────────────────────────────────────────────────────────

def test_trades_without_an_initial_stop_are_counted_but_not_scored():
    s = summarize([_t(5.0), {"symbol": "ZZZ", "r_multiple": None, "reason": "stop"}])
    assert s["n_closed"] == 2
    assert s["n_scored"] == 1
    assert s["n_unscored"] == 1
    assert s["expectancy_r"] == pytest.approx(5.0)   # unscored excluded, not zeroed


# ── breakdowns ─────────────────────────────────────────────────────────────

def test_expectancy_split_by_regime():
    s = summarize([
        _t(6.0, risk_on=True), _t(-1.0, risk_on=True),
        _t(-1.0, risk_on=False), _t(-1.0, risk_on=False),
        _t(2.0),                                        # no regime recorded
    ])
    assert s["by_regime"]["risk_on"]["n"] == 2
    assert s["by_regime"]["risk_on"]["expectancy_r"] == pytest.approx(2.5)
    assert s["by_regime"]["risk_off"]["n"] == 2
    assert s["by_regime"]["risk_off"]["expectancy_r"] == pytest.approx(-1.0)
    assert s["by_regime"]["unknown"]["n"] == 1


def test_exit_reasons_are_counted():
    s = summarize([_t(4.0, reason="trail"), _t(-1.0, reason="gap_stop"),
                   _t(-1.0, reason="stop"), _t(-1.0, reason="stop")])
    assert s["by_reason"] == {"stop": 2, "gap_stop": 1, "trail": 1}


# ── loading ────────────────────────────────────────────────────────────────

def test_load_rows_reads_a_journal_list(tmp_path):
    p = tmp_path / "journal.json"
    p.write_text(json.dumps([_t(1.0), _t(-1.0)]))
    assert len(load_rows(p)) == 2


def test_load_rows_reads_closed_trades_from_a_ledger(tmp_path):
    """The backtest writes a ledger, not a journal — the sweep is read from it."""
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"starting_equity": 200.0, "closed_trades": [_t(3.0)]}))
    rows = load_rows(p)
    assert len(rows) == 1
    assert rows[0]["r_multiple"] == 3.0


def test_load_rows_on_missing_file_is_empty(tmp_path):
    assert load_rows(tmp_path / "nope.json") == []
