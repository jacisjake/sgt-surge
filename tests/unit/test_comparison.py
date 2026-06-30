"""Tests for src/bot/comparison.py — strategy head-to-head metrics."""
from __future__ import annotations

import pytest

from src.bot.comparison import trade_returns, comparison_stats


# ---------------------------------------------------------------------------
# trade_returns
# ---------------------------------------------------------------------------

def test_trade_returns_computes_price_based_fractional_return():
    trades = [
        {"entry_price": 100.0, "exit_price": 110.0},
        {"entry_price": 50.0, "exit_price": 48.0},
    ]
    assert trade_returns(trades, "entry_price", "exit_price") == pytest.approx([0.10, -0.04])


def test_trade_returns_skips_zero_or_missing_entry():
    trades = [
        {"entry_price": 0.0, "exit_price": 5.0},   # zero entry -> skipped
        {"entry_price": None, "exit_price": 5.0},  # missing entry -> skipped
        {"entry_price": 100.0, "exit_price": 105.0},
    ]
    assert trade_returns(trades, "entry_price", "exit_price") == pytest.approx([0.05])


# ---------------------------------------------------------------------------
# comparison_stats
# ---------------------------------------------------------------------------

def test_comparison_stats_basic():
    stats = comparison_stats([0.10, -0.05, 0.02, -0.03])
    assert stats["n_closed"] == 4
    assert stats["win_rate"] == 0.5
    assert abs(stats["avg_win"] - 0.06) < 1e-12
    assert abs(stats["avg_loss"] - (-0.04)) < 1e-12
    assert abs(stats["expectancy"] - 0.01) < 1e-12
    assert abs(stats["norm_return"] - 0.04) < 1e-12


def test_comparison_stats_empty_is_all_zero():
    stats = comparison_stats([])
    assert stats == {
        "n_closed": 0, "win_rate": 0.0, "avg_win": 0.0,
        "avg_loss": 0.0, "expectancy": 0.0, "norm_return": 0.0,
    }


def test_comparison_stats_all_winners_loss_avg_zero():
    stats = comparison_stats([0.05, 0.10])
    assert stats["win_rate"] == 1.0
    assert abs(stats["avg_win"] - 0.075) < 1e-12
    assert stats["avg_loss"] == 0.0
