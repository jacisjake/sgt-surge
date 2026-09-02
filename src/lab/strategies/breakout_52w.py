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
from src.lab.strategies._common import (
    atr_stop_distance,
    build_risk_on,
    chandelier_floor,
    is_fresh_breakout,
    regime_snapshot,
    true_range_atr,
)



class Breakout52wStrategy:
    """Long-only fresh lookback-high breakout with ATR stop + chandelier trail."""


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
        use_ma_exit = bool(params.get("use_ma_exit", False))
        k1 = params.get("k1")
        k2 = float(params.get("k2", 3.0))
        atr_period = int(params.get("atr_period", 14))
        stop_min = float(params.get("stop_min_pct", 0.04))
        stop_max = float(params.get("stop_max_pct", 0.15))
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

            initial_stop = pos.stop_price
            if initial_stop is None:
                initial_stop = pos.avg_entry_price * (1 - stop_pct)
            stop_level = float(initial_stop)
            trailing = False

            if k1 is not None:
                atr_now = _bar_atr(df, i, atr_period)
                if atr_now is not None and atr_now > 0:
                    entry_i = bar_index_for_date(df, pos.entry_date)
                    start = 0 if entry_i is None else entry_i
                    highest_high = max(
                        float(pos.avg_entry_price),
                        float(df["high"].iloc[start : i + 1].max()),
                    )
                    floor = chandelier_floor(highest_high, atr_now, k2)
                    if floor > stop_level:
                        stop_level = floor
                        trailing = True

            open_px = float(df["open"].iloc[i])
            low = float(df["low"].iloc[i])
            close = float(df["close"].iloc[i])

            reason = None
            if open_px <= stop_level:
                reason = "gap_stop"
            elif low <= stop_level:
                reason = "trail" if trailing else "stop"
            elif use_ma_exit:
                sma_exit = df["close"].rolling(ma_exit).mean().iloc[i]
                if (not pd.isna(sma_exit)) and close < float(sma_exit):
                    reason = "trend_break"

            if reason:
                intents.append(
                    OrderIntent(
                        symbol=pos.symbol,
                        side=Side.SELL,
                        reason=reason,
                        stop_price=stop_level,
                        qty=pos.qty,
                        notional=pos.notional,
                        metadata={
                            "entry_price": pos.avg_entry_price,
                            "entry_date": pos.entry_date.isoformat(),
                            "initial_stop": float(initial_stop),
                        },
                    )
                )

        # --- REGIME GATE (entries only) --------------------------------------
        risk_on = self._risk_on(market, params, use_regime_gate, regime_sma, as_of)
        if not risk_on:
            return intents

        # Regime is a property of the entry — recorded so expectancy can be
        # split by regime later. Exits are never tagged.
        regime = regime_snapshot(
            market.extras.get("SPY"), sma_period=regime_sma, as_of=as_of
        )

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
            if k1 is not None:
                atr_now = _bar_atr(df, i, atr_period)
                if atr_now is None or atr_now <= 0:
                    stop_dist = stop_pct
                else:
                    stop_dist = atr_stop_distance(
                        atr_now, entry_price, float(k1), stop_min, stop_max
                    )
                stop_price = entry_price * (1 - stop_dist)
            else:
                stop_price = entry_price * (1 - stop_pct)
            intents.append(
                OrderIntent(
                    symbol=sym,
                    side=Side.BUY,
                    reason="fresh_breakout",
                    stop_price=stop_price,
                    risk_pct=risk_pct,
                    metadata={
                        "entry_price": entry_price,
                        "initial_stop": stop_price,
                        **({"regime": regime} if regime else {}),
                    },
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
        # Callers pass risk_on_override when regime is already evaluated.
        if "risk_on_override" in params and params["risk_on_override"] is not None:
            return bool(params["risk_on_override"])
        if not use_regime_gate:
            return True
        spy = market.extras.get("SPY")
        if spy is None or spy.empty:
            return False
        return build_risk_on(spy, sma_period=regime_sma).get(as_of, False)


def _bar_atr(df: pd.DataFrame, i: int, period: int) -> float | None:
    """ATR at bar i: prefer a provided column (sim.py), else true-range SMA."""
    if "atr" in df.columns:
        val = df["atr"].iloc[i]
        if pd.isna(val):
            return None
        return float(val)
    series = true_range_atr(df, period)
    val = series.iloc[i]
    if pd.isna(val):
        return None
    return float(val)

