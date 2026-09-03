"""BrokerFill — submit OrderIntents via OrderExecutor; never raises on reject."""
from __future__ import annotations

import math
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
    cash_buffer_pct: float = 0.01,
    max_position_pct: float = 0.15,
    max_exposure_pct: float = 0.85,
    existing_notional: float = 0.0,
) -> list[dict]:
    """Convert intents to live_swing-style order dicts with cash sizing.

    ``cash_buffer_pct`` holds back a slice of available cash so a market fill
    that prints above the planning price cannot overdraw the account.

    ``max_position_pct`` caps any single new position at that fraction of
    equity, and ``max_exposure_pct`` caps total invested capital (existing
    holdings, via ``existing_notional``, plus everything planned so far) at
    that fraction of equity. Risk-based sizing alone can put an outsized
    share of the account into one name when its stop is tight; both caps
    size the order down rather than skip it, since the entry signal is
    still valid, just not at that size.
    """
    plan: list[dict] = []
    cash = available_cash
    invested = existing_notional
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
                stop_frac = (price - float(intent.stop_price)) / price
                # A stop at or above entry leaves no risk distance to size
                # against. Flooring it (previously 1e-9) made the risk-based
                # notional explode, so the cash clamp below silently became
                # "spend every available dollar". Skip the entry instead.
                if stop_frac <= 0:
                    continue
            else:
                stop_frac = stop_pct
            spendable = cash * (1.0 - cash_buffer_pct)
            exposure_headroom = max(0.0, max_exposure_pct * equity - invested)
            notional = min(
                risk_pct * equity / stop_frac,
                spendable,
                max_position_pct * equity,
                exposure_headroom,
            )
            if notional < 1.0:
                continue
            # Floor rather than round to the fractional-share precision:
            # rounding up plans more spend than `notional` permitted, which
            # overdraws the account on the very first order.
            qty = math.floor(notional / price * 10_000) / 10_000
            if qty <= 0:
                continue
            actual_notional = qty * price
            plan.append({
                "action": "buy",
                "symbol": intent.symbol,
                "qty": qty,
                "price": price,
                "notional": actual_notional,
                "reason": intent.reason,
                "stop_price": float(intent.stop_price) if intent.stop_price is not None else None,
                # Carried so the live audit can record the regime the trade was
                # entered in; execute_plan spreads this dict into its result.
                "regime": (intent.metadata or {}).get("regime"),
            })

            # Decrement by what is actually planned, not the pre-rounding
            # figure, so remaining cash and exposure stay accurate across
            # orders.
            cash -= actual_notional
            invested += actual_notional
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
