"""Sizing-independent head-to-head metrics for comparing strategies.

Reduces each closed trade to a price-based fractional return (exit/entry - 1),
so two strategies with different position sizing can be compared on edge alone
(win rate, average win/loss, expectancy, equal-weight cumulative return).
"""
from __future__ import annotations


def trade_returns(trades: list[dict], entry_key: str, exit_key: str) -> list[float]:
    """Per-trade fractional return exit/entry - 1 for each long closed trade.

    Trades with a missing or zero entry price are skipped.
    """
    out: list[float] = []
    for t in trades:
        entry = t.get(entry_key)
        exit_ = t.get(exit_key)
        if not entry or exit_ is None:
            continue
        out.append(exit_ / entry - 1.0)
    return out


def comparison_stats(returns: list[float]) -> dict:
    """Edge metrics over a list of per-trade fractional returns.

    norm_return is the equal-weight, one-unit-per-trade, non-compounded sum of
    returns — a sizing-independent proxy for cumulative strategy return.
    """
    n = len(returns)
    if n == 0:
        return {
            "n_closed": 0, "win_rate": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "expectancy": 0.0, "norm_return": 0.0,
        }
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    return {
        "n_closed": n,
        "win_rate": len(wins) / n,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "expectancy": sum(returns) / n,
        "norm_return": sum(returns),
    }
