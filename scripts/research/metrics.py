"""Pure scoring functions over lists of trade R-multiples."""
from __future__ import annotations


def expectancy(rs: list[float]) -> float:
    return sum(rs) / len(rs) if rs else 0.0


def profit_factor(rs: list[float]) -> float:
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def max_drawdown_r(rs: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def max_consecutive_losers(rs: list[float]) -> int:
    run = best = 0
    for r in rs:
        run = run + 1 if r < 0 else 0
        best = max(best, run)
    return best


def summarize(setup: str, rs: list[float]) -> dict:
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    return {
        "setup": setup,
        "n": n,
        "win_pct": (len(wins) / n) if n else 0.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": expectancy(rs),
        "profit_factor": profit_factor(rs),
        "max_drawdown_r": max_drawdown_r(rs),
        "max_consec_losers": max_consecutive_losers(rs),
    }
