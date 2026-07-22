"""SimFill — pin formulas to paper_forward.step (raw prices, ratio PnL)."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from src.lab.ledger import append_equity_snapshot, realized_equity
from src.lab.protocol import MarketContext, OrderIntent, Side, bar_index_for_date, order_exits_before_entries
from src.lab.strategies._common import stop_fill_price


def apply_intents(
    state: dict,
    intents: list[OrderIntent],
    market: MarketContext,
    *,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
    min_notional: float = 1.0,
    snapshot_equity: bool = True,
) -> dict:
    """Apply ordered intents to paper ledger. Mutates and returns *state*.

    Exact slip / PnL model from paper_forward.step:
      slip = 2 * slip_bps / 10_000
      pnl = notional * ((exit*(1-slip))/(entry*(1+slip)) - 1)
    """
    as_of = market.now
    # Idempotency (same as step)
    if state.get("last_date") is not None:
        if as_of <= date.fromisoformat(state["last_date"]):
            return state

    slip = 2 * slip_bps / 10_000
    ordered = order_exits_before_entries(intents)

    # Index open positions by symbol
    open_by_sym = {p["symbol"]: p for p in state["open_positions"]}

    for intent in ordered:
        if intent.side == Side.SELL:
            pos = open_by_sym.get(intent.symbol)
            if pos is None:
                continue
            df = market.bars_by_symbol.get(intent.symbol)
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None:
                continue

            entry_price = float(pos["entry_price"])
            stop_price = float(pos.get("stop_price") or entry_price * (1 - stop_pct))
            notional = float(pos["notional"])
            opens = df["open"].to_numpy()
            closes = df["close"].to_numpy()

            if intent.reason == "stop":
                exit_price = stop_fill_price(stop_price, float(opens[i]))
            else:
                # trend_break / target / time → raw close (or metadata override)
                exit_price = float(intent.metadata.get("exit_price", closes[i]))

            pnl = notional * ((exit_price * (1 - slip)) / (entry_price * (1 + slip)) - 1)
            state["realized_pnl"] += pnl
            state["available_cash"] += notional + pnl
            state["closed_trades"].append(
                {
                    "symbol": intent.symbol,
                    "entry_date": pos["entry_date"],
                    "exit_date": as_of.isoformat(),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "reason": intent.reason,
                }
            )
            del open_by_sym[intent.symbol]

        elif intent.side == Side.BUY:
            if intent.symbol in open_by_sym:
                continue
            df = market.bars_by_symbol.get(intent.symbol)
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None:
                continue

            entry_price = float(intent.metadata.get("entry_price", df["close"].iloc[i]))
            if entry_price <= 0:
                continue

            equity = realized_equity(state)
            risk_pct = float(intent.risk_pct if intent.risk_pct is not None else 0.01)
            # stop distance as fraction of entry
            if intent.stop_price is not None and entry_price > 0:
                stop_frac = max((entry_price - float(intent.stop_price)) / entry_price, 1e-9)
            else:
                stop_frac = stop_pct

            notional = min(risk_pct * equity / stop_frac, state["available_cash"])
            if intent.notional is not None:
                notional = min(notional, float(intent.notional))
            if notional < min_notional:
                continue

            stop_price = (
                float(intent.stop_price)
                if intent.stop_price is not None
                else entry_price * (1 - stop_pct)
            )
            open_by_sym[intent.symbol] = {
                "symbol": intent.symbol,
                "entry_date": as_of.isoformat(),
                "entry_price": entry_price,
                "stop_price": stop_price,
                "notional": notional,
            }
            state["available_cash"] -= notional

    state["open_positions"] = list(open_by_sym.values())
    state["last_date"] = as_of.isoformat()
    if snapshot_equity:
        append_equity_snapshot(state, as_of)
    return state
