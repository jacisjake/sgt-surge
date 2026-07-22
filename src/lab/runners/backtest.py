"""BacktestRunner — day-step engine identical to paper SimFill."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import new_state, portfolio_from_paper
from src.lab.protocol import MarketContext
from src.lab.promote import write_backtest_report
from src.lab.strategies import get_strategy
from src.lab.strategies._common import build_risk_on


def _session_dates(bars_by_symbol: dict[str, pd.DataFrame]) -> list[date]:
    dates: set[date] = set()
    for df in bars_by_symbol.values():
        for ts in df.index:
            dates.add(ts.date())
    return sorted(dates)


def run_day_step_backtest(
    strategy_name: str,
    bars_by_symbol: dict[str, pd.DataFrame],
    params: dict[str, Any],
    *,
    capital: float = 200.0,
    spy_df: Optional[pd.DataFrame] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    """Replay SimFill day-by-day. Returns final state + summary metrics."""
    strategy = get_strategy(strategy_name)
    state = new_state(starting_equity=capital)
    stop_pct = float(params.get("stop_pct", 0.08))
    slip_bps = float(params.get("slip_bps", 15.0))
    use_regime = bool(params.get("use_regime_gate", False))
    regime_sma = int(params.get("regime_sma", 200))

    sessions = _session_dates(bars_by_symbol)
    if start:
        sessions = [d for d in sessions if d >= start]
    if end:
        sessions = [d for d in sessions if d <= end]

    for session in sessions:
        # Slice bars up to and including session (causal)
        sliced = {
            sym: df[df.index.map(lambda ts: ts.date() <= session)]
            for sym, df in bars_by_symbol.items()
            if not df.empty
        }
        sliced = {s: d for s, d in sliced.items() if not d.empty and d.index[-1].date() >= session}
        if not sliced:
            continue

        extras: dict[str, pd.DataFrame] = {}
        plan_params = dict(params)
        if use_regime:
            if spy_df is None or spy_df.empty:
                plan_params["risk_on_override"] = False
            else:
                spy_sliced = spy_df[spy_df.index.map(lambda ts: ts.date() <= session)]
                extras["SPY"] = spy_sliced
                plan_params["risk_on_override"] = build_risk_on(
                    spy_sliced, sma_period=regime_sma
                ).get(session, False)

        portfolio = portfolio_from_paper(state, session)
        market = MarketContext(bars_by_symbol=sliced, extras=extras, now=session)
        intents = strategy.plan(portfolio, market, plan_params)
        state = apply_intents(
            state,
            intents,
            market,
            stop_pct=stop_pct,
            slip_bps=slip_bps,
            snapshot_equity=True,
        )

    final_eq = float(state["starting_equity"]) + float(state["realized_pnl"])
    n_taken = len(state.get("closed_trades") or [])
    pnls = [float(t["pnl"]) for t in state.get("closed_trades") or []]
    expectancy = sum(pnls) / n_taken if n_taken else 0.0
    # max DD from equity curve
    curve = state.get("equity_curve_daily") or []
    peak = capital
    max_dd = 0.0
    for row in curve:
        e = float(row["equity_realized"])
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)

    return {
        "state": state,
        "metrics": {
            "final_equity": final_eq,
            "total_return": (final_eq / capital - 1.0) if capital else 0.0,
            "max_drawdown": max_dd,
            "n_taken": n_taken,
            "expectancy": expectancy,
            "engine": "day_step",
        },
        "window": {
            "start": sessions[0].isoformat() if sessions else None,
            "end": sessions[-1].isoformat() if sessions else None,
        },
    }


def write_report_from_backtest(
    path: str,
    *,
    strategy: str,
    params: dict,
    result: dict[str, Any],
) -> None:
    report = {
        "strategy": strategy,
        "params": params,
        "window": result["window"],
        "metrics": result["metrics"],
        "artifact_path": path,
    }
    write_backtest_report(path, report)
