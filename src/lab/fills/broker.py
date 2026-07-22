"""BrokerFill — submit OrderIntents via OrderExecutor; never raises on reject."""
from __future__ import annotations

from typing import Any

from src.lab.protocol import OrderIntent, Side, order_exits_before_entries


def intents_to_live_plan(
    intents: list[OrderIntent],
    *,
    equity: float,
    available_cash: float,
    stop_pct: float,
    prices: dict[str, float],
    risk_pct_default: float = 0.01,
) -> list[dict]:
    """Convert intents to live_swing-style order dicts with cash sizing."""
    plan: list[dict] = []
    cash = available_cash
    for intent in order_exits_before_entries(intents):
        price = float(prices.get(intent.symbol) or intent.metadata.get("entry_price") or 0)
        if intent.side == Side.SELL:
            qty = float(intent.qty or 0.0)
            plan.append({
                "action": "sell",
                "symbol": intent.symbol,
                "qty": qty,
                "price": price,
                "notional": qty * price,
                "reason": intent.reason,
            })
        elif intent.side == Side.BUY:
            if price <= 0:
                continue
            risk_pct = float(intent.risk_pct if intent.risk_pct is not None else risk_pct_default)
            if intent.stop_price is not None:
                stop_frac = max((price - float(intent.stop_price)) / price, 1e-9)
            else:
                stop_frac = stop_pct
            notional = min(risk_pct * equity / stop_frac, cash)
            if notional < 1.0:
                continue
            qty = round(notional / price, 4)
            if qty <= 0:
                continue
            plan.append({
                "action": "buy",
                "symbol": intent.symbol,
                "qty": qty,
                "price": price,
                "notional": qty * price,
                "reason": intent.reason,
            })
            cash -= notional
    return plan


def execute_plan(plan: list[dict], executor) -> list[dict]:
    """Place each order. Returns per-order results; never raises."""
    results: list[dict[str, Any]] = []
    for o in plan:
        try:
            res = executor.execute_market_order(
                o["symbol"], o["qty"], o["action"], wait_for_fill=False
            )
            results.append({**o, "status": "submitted", "result": str(res)})
        except Exception as e:  # noqa: BLE001
            results.append({
                **o,
                "status": "rejected",
                "error": f"{type(e).__name__}: {e}",
            })
    return results
