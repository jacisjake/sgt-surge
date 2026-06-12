"""Tests for swing/portfolio.py — written FIRST (TDD red phase)."""
from __future__ import annotations

import datetime

import pytest

from scripts.research.swing.portfolio import simulate_portfolio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(entry_date, exit_date, return_pct, stop_pct=0.05, symbol="SYM"):
    """Build a minimal trade dict."""
    return {
        "symbol": symbol,
        "entry_date": datetime.date.fromisoformat(entry_date),
        "exit_date": datetime.date.fromisoformat(exit_date),
        "return_pct": return_pct,
        "stop_pct": stop_pct,
    }


# ---------------------------------------------------------------------------
# Return-key contract
# ---------------------------------------------------------------------------

def test_simulate_portfolio_has_required_keys():
    """simulate_portfolio returns a dict with all required keys."""
    result = simulate_portfolio([], starting_equity=200.0)
    required = {
        "starting_equity", "final_equity", "total_return",
        "n_taken", "n_skipped", "max_drawdown",
        "worst_trade_pnl", "best_trade_pnl", "equity_curve",
    }
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"


def test_simulate_portfolio_empty_trades_returns_starting_equity():
    """No trades: final_equity == starting_equity, all counters zero."""
    result = simulate_portfolio([], starting_equity=300.0)
    assert result["starting_equity"] == 300.0
    assert result["final_equity"] == 300.0
    assert result["total_return"] == 0.0
    assert result["n_taken"] == 0
    assert result["n_skipped"] == 0
    assert result["max_drawdown"] == 0.0
    assert result["equity_curve"] == []


# ---------------------------------------------------------------------------
# Two non-overlapping winning trades compound equity correctly
# ---------------------------------------------------------------------------

def test_two_non_overlapping_wins_compound():
    """Hand-compute two non-overlapping wins; verify final_equity and n_taken.

    starting_equity = 200, risk_pct=0.01, stop_pct=0.05.
    notional = 0.01 * equity / 0.05 = 0.2 * equity.

    Trade 1: entry 2025-01-02, exit 2025-01-07, return_pct=+0.10
      notional_1 = 0.2 * 200 = 40.0
      pnl_1 = 40.0 * 0.10 = 4.0
      equity after exit = 200 + 4.0 = 204.0

    Trade 2: entry 2025-01-10, exit 2025-01-15, return_pct=+0.10
      notional_2 = 0.2 * 204.0 = 40.8
      pnl_2 = 40.8 * 0.10 = 4.08
      equity after exit = 204.0 + 4.08 = 208.08
    """
    trades = [
        _trade("2025-01-02", "2025-01-07", return_pct=0.10),
        _trade("2025-01-10", "2025-01-15", return_pct=0.10),
    ]
    result = simulate_portfolio(trades, starting_equity=200.0, risk_pct=0.01)

    assert result["n_taken"] == 2
    assert result["n_skipped"] == 0
    assert abs(result["final_equity"] - 208.08) < 1e-9
    assert abs(result["total_return"] - (208.08 / 200.0 - 1.0)) < 1e-9


# ---------------------------------------------------------------------------
# A losing trade reduces equity; max_drawdown reflects it
# ---------------------------------------------------------------------------

def test_losing_trade_reduces_equity_and_max_drawdown_nonzero():
    """A single losing trade: equity drops and max_drawdown > 0.

    starting_equity=200, risk_pct=0.01, stop_pct=0.05.
    notional = 0.2 * 200 = 40.0
    return_pct = -0.05  (stopped out exactly)
    pnl = 40.0 * (-0.05) = -2.0
    final_equity = 198.0
    max_drawdown = 2.0 / 200.0 = 0.01
    """
    trades = [_trade("2025-01-02", "2025-01-07", return_pct=-0.05)]
    result = simulate_portfolio(trades, starting_equity=200.0, risk_pct=0.01)

    assert result["n_taken"] == 1
    assert abs(result["final_equity"] - 198.0) < 1e-9
    assert result["max_drawdown"] > 0.0
    assert abs(result["max_drawdown"] - 0.01) < 1e-9


# ---------------------------------------------------------------------------
# Capital cap: many same-day entries → n_skipped > 0
# ---------------------------------------------------------------------------

def test_capital_cap_limits_concurrent_positions():
    """Many trades entering on the same day with tiny starting_equity
    → some are skipped when available_cash < min_notional.

    starting_equity=10, risk_pct=0.01, stop_pct=0.05, min_notional=1.0.
    notional per trade = 0.01 * 10 / 0.05 = 2.0.
    So at most floor(10 / 2.0) = 5 can be opened; rest skipped.
    """
    trades = [
        _trade("2025-01-02", "2025-01-09", return_pct=0.10, symbol=f"SYM{i}")
        for i in range(10)
    ]
    result = simulate_portfolio(trades, starting_equity=10.0, risk_pct=0.01,
                                min_notional=1.0)
    assert result["n_skipped"] > 0
    assert result["n_taken"] + result["n_skipped"] == 10
    # taken + skipped add up correctly
    assert result["n_taken"] <= 5  # can't fit more than 5 @ notional=2 in 10 cash


def test_max_concurrent_cap_respected():
    """max_concurrent=2 limits concurrent open positions to at most 2.

    7 trades all enter on the same date → only 2 taken, 5 skipped.
    """
    trades = [
        _trade("2025-01-02", "2025-01-09", return_pct=0.10, symbol=f"S{i}")
        for i in range(7)
    ]
    result = simulate_portfolio(trades, starting_equity=500.0, risk_pct=0.01,
                                max_concurrent=2)
    assert result["n_taken"] == 2
    assert result["n_skipped"] == 5


# ---------------------------------------------------------------------------
# max_drawdown on a known equity sequence
# ---------------------------------------------------------------------------

def test_max_drawdown_hand_computed():
    """Verify max_drawdown matches a hand-computed peak-to-trough fraction.

    Arrange three trades so the equity curve visits known values:
      [200 -> 210 -> 205 -> 212]

    starting_equity=200, risk_pct=0.01, stop_pct=0.05 -> notional=0.2*equity.

    Trade A: entry 2025-01-02, exit 2025-01-05, return_pct=+0.10
      notional_A = 0.2 * 200 = 40; pnl = 4.0; equity -> 204.0
      ... but let me use exact numbers to get 210, 205, 212.

    Use a different parameterisation:
      starting_equity=1000, risk_pct=0.05, stop_pct=0.05
      => notional = 0.05/0.05 * equity = 1.0 * equity  (bet the whole account)

    That's too unstable. Instead fix notional directly via very small stop_pct.

    Cleaner: use risk_pct=0.01, stop_pct=0.01  => notional = equity.
    Trade A: +0.10  -> pnl = equity * 0.10;  equity 1000 -> 1100  (peak=1100)
    Trade B: -0.05  -> pnl = 1100 * (-0.05) = -55; equity -> 1045 (trough=1045)
    Trade C: +0.10  -> pnl = 1045 * 0.10 = 104.5; equity -> 1149.5

    max_drawdown = (1100 - 1045) / 1100 = 55/1100 = 0.05
    """
    trades = [
        _trade("2025-01-02", "2025-01-05", return_pct=+0.10),  # A
        _trade("2025-01-07", "2025-01-10", return_pct=-0.05),  # B
        _trade("2025-01-12", "2025-01-15", return_pct=+0.10),  # C
    ]
    result = simulate_portfolio(trades, starting_equity=1000.0,
                                risk_pct=0.01, max_concurrent=None)
    # notional each time = 0.01 * eq / 0.05 = 0.2 * eq (default stop_pct=0.05)
    # A: notional=200, pnl=20  -> eq=1020
    # B: notional=0.2*1020=204, pnl=204*(-0.05)=-10.2 -> eq=1009.8
    # C: notional=0.2*1009.8=201.96, pnl=201.96*0.10=20.196 -> eq=1029.996
    # peak after A = 1020; trough after B = 1009.8
    # max_drawdown = (1020 - 1009.8) / 1020 = 10.2/1020 = 0.01
    expected_dd = 10.2 / 1020.0
    assert abs(result["max_drawdown"] - expected_dd) < 1e-9


def test_max_drawdown_no_loss_is_zero():
    """Only winning trades: max_drawdown == 0."""
    trades = [
        _trade("2025-01-02", "2025-01-05", return_pct=+0.05),
        _trade("2025-01-07", "2025-01-10", return_pct=+0.08),
    ]
    result = simulate_portfolio(trades, starting_equity=200.0, risk_pct=0.01)
    assert result["max_drawdown"] == 0.0


# ---------------------------------------------------------------------------
# best/worst trade pnl
# ---------------------------------------------------------------------------

def test_best_and_worst_trade_pnl():
    """best_trade_pnl > 0, worst_trade_pnl < 0, correct sign."""
    trades = [
        _trade("2025-01-02", "2025-01-05", return_pct=+0.10),
        _trade("2025-01-07", "2025-01-10", return_pct=-0.05),
        _trade("2025-01-12", "2025-01-15", return_pct=+0.02),
    ]
    result = simulate_portfolio(trades, starting_equity=200.0, risk_pct=0.01)
    assert result["best_trade_pnl"] > 0.0
    assert result["worst_trade_pnl"] < 0.0
    # best > worst
    assert result["best_trade_pnl"] > result["worst_trade_pnl"]


def test_best_worst_pnl_empty_trades():
    """No trades: best and worst pnl are 0.0."""
    result = simulate_portfolio([], starting_equity=200.0)
    assert result["best_trade_pnl"] == 0.0
    assert result["worst_trade_pnl"] == 0.0


# ---------------------------------------------------------------------------
# equity_curve
# ---------------------------------------------------------------------------

def test_equity_curve_length_equals_n_taken():
    """equity_curve has one entry per taken trade (appended at exit)."""
    trades = [
        _trade("2025-01-02", "2025-01-05", return_pct=0.10),
        _trade("2025-01-07", "2025-01-10", return_pct=0.05),
    ]
    result = simulate_portfolio(trades, starting_equity=200.0, risk_pct=0.01)
    assert len(result["equity_curve"]) == result["n_taken"]


def test_exits_before_entries_same_day_frees_capital():
    """Exits on day D happen before entries on day D free up capital.

    Trade A: entry 2025-01-02, exit 2025-01-05, small notional.
    Trade B: entry 2025-01-05 (same exit day as A), can be funded by A's returned capital.

    Use starting_equity barely enough for one trade:
      notional = risk_pct * equity / stop_pct
      equity=10, risk_pct=0.01, stop_pct=0.05 -> notional=2.0
      After funding A: remaining cash=8.0.
      For B (also entry 2025-01-05): equity still 10 (unrealized), notional=2.
      But only 8 cash remains.  A exits on 2025-01-05 (+pnl), freeing notional+pnl
      BEFORE B's entry is processed.
      If we start with equity=2.0 (only enough for one at a time):
        notional=0.4, cash after A=1.6. B can only open if A exits first.
    """
    starting_equity = 2.0
    # notional = 0.01 * 2.0 / 0.05 = 0.4
    # A is entered on Jan 2, exits Jan 5.
    # B is entered on Jan 5, exits Jan 8.
    # If exits run before entries on Jan 5, B can be funded by A's returned cash.
    trades = [
        _trade("2025-01-02", "2025-01-05", return_pct=0.0, symbol="A"),  # break-even
        _trade("2025-01-05", "2025-01-08", return_pct=0.0, symbol="B"),
    ]
    result = simulate_portfolio(trades, starting_equity=starting_equity,
                                risk_pct=0.01, min_notional=0.01)
    # Both trades should be taken (A exits before B enters on same day)
    assert result["n_taken"] == 2
    assert result["n_skipped"] == 0
