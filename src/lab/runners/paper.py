"""PaperRunner — day-step SimFill for a registered experiment."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import load_state, portfolio_from_paper, save_state
from src.lab.protocol import MarketContext
from src.lab.registry import Experiment, resolve_ledger_path
from src.lab.strategies import get_strategy
from src.lab.strategies._common import build_risk_on


def _read_symbols(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [s.strip().upper() for s in p.read_text().split() if s.strip()]


def run_paper_day(
    exp: Experiment,
    client,
    *,
    as_of: Optional[date] = None,
    state_path: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch bars, plan, SimFill, persist ledger. Returns summary dict."""
    params = dict(exp.params)
    lookback = int(params.get("lookback", 252))
    slip_bps = float(params.get("slip_bps", 15.0))
    stop_pct = float(params.get("stop_pct", 0.08))
    use_regime_gate = bool(params.get("use_regime_gate", True))
    regime_sma = int(params.get("regime_sma", 200))

    path = state_path or resolve_ledger_path(exp)
    symbols = _read_symbols(exp.symbols_file)
    if not symbols:
        return {"ok": False, "error": f"no symbols in {exp.symbols_file}", "ledger": path}

    today_wall = as_of or date.today()
    fetch_start = today_wall - timedelta(days=(lookback + 30) * 2)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, today_wall)
        if df is not None and not df.empty:
            bars_by_symbol[sym] = df
    if not bars_by_symbol:
        return {"ok": False, "error": "no bars fetched", "ledger": path}

    session = as_of or max(df.index[-1].date() for df in bars_by_symbol.values())

    extras: dict[str, pd.DataFrame] = {}
    risk_on_override = None
    if use_regime_gate:
        spy = client.get_history("SPY", "1Day", fetch_start, today_wall)
        if spy is None or spy.empty:
            risk_on_override = False
        else:
            extras["SPY"] = spy
            risk_on_override = build_risk_on(spy, sma_period=regime_sma).get(session, False)

    state = load_state(path, starting_equity=exp.capital)
    if "experiment_id" not in state:
        state["experiment_id"] = exp.id
        state["strategy"] = exp.strategy
        state["params"] = params

    portfolio = portfolio_from_paper(state, session)
    market = MarketContext(bars_by_symbol=bars_by_symbol, extras=extras, now=session)
    plan_params = dict(params)
    if risk_on_override is not None:
        plan_params["risk_on_override"] = risk_on_override

    strategy = get_strategy(exp.strategy)
    intents = strategy.plan(portfolio, market, plan_params)
    prev_closed = len(state["closed_trades"])
    prev_open = len(state["open_positions"])
    state = apply_intents(
        state,
        intents,
        market,
        stop_pct=stop_pct,
        slip_bps=slip_bps,
        snapshot_equity=True,
    )
    save_state(path, state)
    return {
        "ok": True,
        "date": session.isoformat(),
        "ledger": path,
        "equity": state["starting_equity"] + state["realized_pnl"],
        "open_positions": len(state["open_positions"]),
        "new_entries": len(state["open_positions"]) - prev_open + max(0, len(state["closed_trades"]) - prev_closed),
        "new_exits": len(state["closed_trades"]) - prev_closed,
        "intents": [
            {"symbol": i.symbol, "side": i.side.value, "reason": i.reason}
            for i in intents
        ],
    }
