"""Short-term reversal (dip-buy) — pure plan() with sessions_after_entry."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from src.lab.protocol import (
    MarketContext,
    OrderIntent,
    PortfolioView,
    Side,
    bar_index_for_date,
)


def sessions_after_entry(as_of: date, entry_date: date, symbol_bars: pd.DataFrame) -> int:
    """Count bar dates in (entry_date, as_of] (strictly after entry, including as_of)."""
    n = 0
    for ts in symbol_bars.index:
        d = ts.date()
        if entry_date < d <= as_of:
            n += 1
    return n


class ShortTermReversalStrategy:
    """Buy oversold dips above MA; exit stop / target / time (non-overlap per symbol)."""

    name = "short_term_reversal"

    def plan(
        self,
        portfolio: PortfolioView,
        market: MarketContext,
        params: dict[str, Any],
    ) -> list[OrderIntent]:
        down_days = int(params.get("down_days", 3))
        hold = int(params.get("hold", 5))
        stop_pct = float(params.get("stop_pct", 0.05))
        target_pct = float(params.get("target_pct", 0.10))
        ma = int(params.get("ma", 200))
        risk_pct = float(params.get("risk_pct", 0.01))
        as_of = market.now

        intents: list[OrderIntent] = []

        # --- EXITS -----------------------------------------------------------
        for pos in portfolio.positions:
            df = market.bars_by_symbol.get(pos.symbol)
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None:
                continue

            n_sess = sessions_after_entry(as_of, pos.entry_date, df)
            # Entry day: no exits (matches research j starting at i+1)
            if n_sess < 1:
                continue

            stop_price = pos.stop_price
            if stop_price is None:
                stop_price = pos.avg_entry_price * (1 - stop_pct)
            target_price = (pos.metadata or {}).get("target_price")
            if target_price is None:
                target_price = pos.avg_entry_price * (1 + target_pct)
            hold_bars = int((pos.metadata or {}).get("hold_bars", hold))

            low = float(df["low"].iloc[i])
            high = float(df["high"].iloc[i])
            close = float(df["close"].iloc[i])

            reason = None
            exit_meta: dict[str, Any] = {
                "entry_price": pos.avg_entry_price,
                "entry_date": pos.entry_date.isoformat(),
            }
            if low <= stop_price:
                reason = "stop"
            elif high >= float(target_price):
                reason = "target"
                exit_meta["exit_price"] = float(target_price)
            elif n_sess >= hold_bars:
                reason = "time"
                exit_meta["exit_price"] = close

            if reason:
                intents.append(
                    OrderIntent(
                        symbol=pos.symbol,
                        side=Side.SELL,
                        reason=reason,
                        stop_price=stop_price,
                        target_price=float(target_price),
                        qty=pos.qty,
                        notional=pos.notional,
                        metadata=exit_meta,
                    )
                )

        held = {p.symbol for p in portfolio.positions}
        selling = {it.symbol for it in intents if it.side == Side.SELL}

        # --- ENTRIES (non-overlap per symbol) --------------------------------
        for sym, df in market.bars_by_symbol.items():
            if sym in held or sym in selling:
                continue
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None or i < max(down_days, ma - 1):
                continue

            closes = df["close"].to_numpy()
            sma = df["close"].rolling(ma).mean().to_numpy()
            if pd.isna(sma[i]) or closes[i] <= sma[i]:
                continue
            decreasing = all(
                closes[i - k] < closes[i - k - 1] for k in range(down_days)
            )
            if not decreasing:
                continue

            entry_price = float(closes[i])
            if entry_price <= 0:
                continue
            stop_price = entry_price * (1 - stop_pct)
            target_price = entry_price * (1 + target_pct)
            intents.append(
                OrderIntent(
                    symbol=sym,
                    side=Side.BUY,
                    reason="short_term_reversal",
                    stop_price=stop_price,
                    target_price=target_price,
                    risk_pct=risk_pct,
                    metadata={
                        "entry_price": entry_price,
                        "target_price": target_price,
                        "hold_bars": hold,
                    },
                )
            )

        return intents
