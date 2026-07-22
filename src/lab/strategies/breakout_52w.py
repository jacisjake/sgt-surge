"""52-week-high breakout strategy — pure plan() decisions."""
from __future__ import annotations

from typing import Any

import pandas as pd

from src.lab.protocol import (
    MarketContext,
    OrderIntent,
    PortfolioView,
    Side,
    bar_index_for_date,
)
from src.lab.strategies._common import build_risk_on, is_fresh_breakout


class Breakout52wStrategy:
    """Long-only fresh lookback-high breakout with hard stop + MA trend-break exit."""

    name = "breakout_52w"

    def plan(
        self,
        portfolio: PortfolioView,
        market: MarketContext,
        params: dict[str, Any],
    ) -> list[OrderIntent]:
        lookback = int(params.get("lookback", 252))
        ma_exit = int(params.get("ma_exit", 50))
        stop_pct = float(params.get("stop_pct", 0.08))
        risk_pct = float(params.get("risk_pct", 0.01))
        regime_sma = int(params.get("regime_sma", 200))
        use_regime_gate = bool(params.get("use_regime_gate", True))
        as_of = market.now

        intents: list[OrderIntent] = []

        # --- EXITS (always; never gated by regime) ---------------------------
        for pos in portfolio.positions:
            df = market.bars_by_symbol.get(pos.symbol)
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None:
                continue

            stop_price = pos.stop_price
            if stop_price is None:
                stop_price = pos.avg_entry_price * (1 - stop_pct)

            low = float(df["low"].iloc[i])
            close = float(df["close"].iloc[i])
            sma_exit = df["close"].rolling(ma_exit).mean().iloc[i]

            reason = None
            if low <= stop_price:
                reason = "stop"
            elif (not pd.isna(sma_exit)) and close < float(sma_exit):
                reason = "trend_break"

            if reason:
                intents.append(
                    OrderIntent(
                        symbol=pos.symbol,
                        side=Side.SELL,
                        reason=reason,
                        stop_price=stop_price,
                        qty=pos.qty,
                        notional=pos.notional,
                        metadata={
                            "entry_price": pos.avg_entry_price,
                            "entry_date": pos.entry_date.isoformat(),
                        },
                    )
                )

        # --- REGIME GATE (entries only) --------------------------------------
        risk_on = self._risk_on(market, params, use_regime_gate, regime_sma, as_of)
        if not risk_on:
            return intents

        held = {p.symbol for p in portfolio.positions}
        selling = {it.symbol for it in intents if it.side == Side.SELL}

        # --- ENTRIES ---------------------------------------------------------
        for sym, df in market.bars_by_symbol.items():
            if sym in held or sym in selling:
                continue
            if df is None or df.empty:
                continue
            i = bar_index_for_date(df, as_of)
            if i is None or i < lookback:
                continue

            highs = df["high"].to_numpy()
            closes = df["close"].to_numpy()
            if not is_fresh_breakout(highs, closes, i, lookback):
                continue

            entry_price = float(closes[i])
            if entry_price <= 0:
                continue
            stop_price = entry_price * (1 - stop_pct)
            intents.append(
                OrderIntent(
                    symbol=sym,
                    side=Side.BUY,
                    reason="fresh_breakout",
                    stop_price=stop_price,
                    risk_pct=risk_pct,
                    metadata={"entry_price": entry_price},
                )
            )

        return intents

    @staticmethod
    def _risk_on(
        market: MarketContext,
        params: dict[str, Any],
        use_regime_gate: bool,
        regime_sma: int,
        as_of,
    ) -> bool:
        # Paper step() passes risk_on_override when regime already evaluated.
        if "risk_on_override" in params and params["risk_on_override"] is not None:
            return bool(params["risk_on_override"])
        if not use_regime_gate:
            return True
        spy = market.extras.get("SPY")
        if spy is None or spy.empty:
            return False
        return build_risk_on(spy, sma_period=regime_sma).get(as_of, False)
