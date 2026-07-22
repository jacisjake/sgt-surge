"""LiveRunner — broker open-set SoT; lab audit ledger for orders/idempotency."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.lab.fills.broker import execute_plan, intents_to_live_plan
from src.lab.protocol import MarketContext, PositionView, PortfolioView, bar_index_for_date
from src.lab.registry import Experiment, assert_can_run, load_registry
from src.lab.strategies import get_strategy
from src.lab.strategies._common import build_risk_on


def _audit_path(exp: Experiment) -> Path:
    if exp.live_audit_path:
        return Path(exp.live_audit_path)
    return Path(f"state/experiments/{exp.id}/live_audit.json")


def load_audit(path: Path) -> dict:
    if not path.exists():
        return {
            "experiment_id": None,
            "last_date": None,
            "orders": [],
            "position_meta": {},
        }
    return json.loads(path.read_text())


def save_audit(path: Path, audit: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2) + "\n")


def broker_to_views(
    positions: list[dict],
    *,
    as_of: date,
    stop_pct: float,
    audit_meta: dict,
) -> list[PositionView]:
    views: list[PositionView] = []
    for p in positions:
        sym = p["symbol"]
        avg = float(p["avg_entry_price"])
        qty = float(p["qty"])
        meta = dict(audit_meta.get(sym) or {})
        entry_date = as_of
        if meta.get("entry_date"):
            entry_date = date.fromisoformat(meta["entry_date"])
        views.append(
            PositionView(
                symbol=sym,
                qty=qty,
                avg_entry_price=avg,
                entry_date=entry_date,
                stop_price=avg * (1 - stop_pct),
                notional=qty * avg,
                metadata=meta,
            )
        )
    return views


def run_live_day(
    exp: Experiment,
    client,
    executor,
    experiments: dict[str, Experiment],
    *,
    as_of: Optional[date] = None,
    preview: bool = True,
    trading_mode: str = "live",
    enable_orb_live: bool = False,
) -> dict[str, Any]:
    """Plan (and optionally submit) live orders for one session."""
    assert_can_run(
        experiments,
        exp,
        "live",
        trading_mode,
        preview=preview,
        enable_orb_live=enable_orb_live,
    )

    params = dict(exp.params)
    lookback = int(params.get("lookback", 252))
    stop_pct = float(params.get("stop_pct", 0.08))
    risk_pct = float(params.get("risk_pct", 0.01))
    use_regime_gate = bool(params.get("use_regime_gate", True))
    regime_sma = int(params.get("regime_sma", 200))

    audit_path = _audit_path(exp)
    audit = load_audit(audit_path)
    audit["experiment_id"] = exp.id

    symbols_path = Path(exp.symbols_file)
    symbols = [
        s.strip().upper()
        for s in symbols_path.read_text().split()
        if s.strip()
    ] if symbols_path.exists() else []

    today_wall = as_of or date.today()
    fetch_start = today_wall - timedelta(days=(lookback + 30) * 2)
    bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, today_wall)
        if df is not None and not df.empty:
            bars[sym] = df
    if not bars:
        return {"ok": False, "error": "no bars", "preview": preview}

    session = as_of or max(df.index[-1].date() for df in bars.values())

    # Idempotency: one live submit per session date
    if (
        not preview
        and audit.get("last_date") is not None
        and session <= date.fromisoformat(audit["last_date"])
    ):
        return {
            "ok": True,
            "skipped": True,
            "reason": "already ran for date",
            "date": session.isoformat(),
        }

    extras: dict[str, pd.DataFrame] = {}
    if use_regime_gate:
        spy = client.get_history("SPY", "1Day", fetch_start, today_wall)
        if spy is not None and not spy.empty:
            extras["SPY"] = spy

    positions = client.get_positions()
    acct = client.get_account()
    equity = float(acct["equity"])
    cash = float(acct["buying_power"])

    portfolio = PortfolioView(
        as_of=session,
        equity=equity,
        available_cash=cash,
        positions=broker_to_views(
            positions,
            as_of=session,
            stop_pct=stop_pct,
            audit_meta=audit.get("position_meta") or {},
        ),
    )
    market = MarketContext(bars_by_symbol=bars, extras=extras, now=session)
    intents = get_strategy(exp.strategy).plan(portfolio, market, params)

    prices: dict[str, float] = {}
    for sym, df in bars.items():
        i = bar_index_for_date(df, session)
        if i is not None:
            prices[sym] = float(df["close"].iloc[i])

    plan = intents_to_live_plan(
        intents,
        equity=equity,
        available_cash=cash,
        stop_pct=stop_pct,
        prices=prices,
        risk_pct_default=risk_pct,
    )

    result: dict[str, Any] = {
        "ok": True,
        "date": session.isoformat(),
        "preview": preview,
        "equity": equity,
        "cash": cash,
        "plan": plan,
        "intents": [
            {"symbol": i.symbol, "side": i.side.value, "reason": i.reason}
            for i in intents
        ],
    }

    if preview:
        return result

    run_id = datetime.now(timezone.utc).isoformat()
    results = execute_plan(plan, executor)
    result["results"] = results

    # Audit append + position metadata for buys
    for r in results:
        audit.setdefault("orders", []).append({
            "run_id": run_id,
            "date": session.isoformat(),
            "symbol": r["symbol"],
            "side": r["action"],
            "qty": r["qty"],
            "reason": r.get("reason"),
            "status": r.get("status"),
            "error": r.get("error"),
        })
        if r.get("status") == "submitted" and r["action"] == "buy":
            meta = audit.setdefault("position_meta", {})
            meta[r["symbol"]] = {
                "entry_date": session.isoformat(),
                "strategy": exp.strategy,
            }
        if r.get("status") == "submitted" and r["action"] == "sell":
            audit.setdefault("position_meta", {}).pop(r["symbol"], None)

    audit["last_date"] = session.isoformat()
    audit["last_run_id"] = run_id
    save_audit(audit_path, audit)
    result["audit_path"] = str(audit_path)
    return result
