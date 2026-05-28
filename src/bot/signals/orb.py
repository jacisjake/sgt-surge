"""
Opening Range Breakout — long-only strategy.

Locks the 9:30-9:45 ET range from REST pricehistory at 9:45:30 ET, then watches
streaming 5-min bars for a close above the OR high (with bar volume >= 1/3 of
OR volume). Emits a single Signal per symbol per day; exit logic is handled
by monitor.py + position_manager.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import pandas as pd
import pytz

from src.bot.signals.base import Signal, SignalDirection, SignalGenerator


ET = pytz.timezone("America/New_York")
ENTRY_CUTOFF_ET = time(15, 15)


@dataclass
class _ORState:
    or_high: float = 0.0
    or_low: float = 0.0
    or_volume: int = 0
    or_locked: bool = False
    breakout_fired: bool = False


class OpeningRangeBreakout(SignalGenerator):
    """ORB-15 (9:30-9:45 ET) strategy."""

    def __init__(self, *, target_r: float = 2.0):
        self.target_r = target_r
        self.state: dict[str, _ORState] = {}

    def register(self, symbol: str) -> None:
        self.state.setdefault(symbol, _ORState())

    def lock_or(self, symbol: str, or_bars: pd.DataFrame) -> None:
        st = self.state.setdefault(symbol, _ORState())
        if or_bars.empty:
            return
        st.or_high = float(or_bars["high"].max())
        st.or_low = float(or_bars["low"].min())
        st.or_volume = int(or_bars["volume"].sum())
        st.or_locked = True

    def on_bar(self, bar: dict) -> Optional[Signal]:
        symbol = bar["symbol"]
        st = self.state.get(symbol)
        if st is None or not st.or_locked or st.breakout_fired:
            return None

        ts = datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00"))
        if ts.astimezone(ET).time() >= ENTRY_CUTOFF_ET:
            return None

        if bar["close"] <= st.or_high:
            return None
        if bar["volume"] < st.or_volume / 3:
            return None

        entry = float(bar["close"])
        risk = entry - st.or_low
        target = entry + self.target_r * risk
        st.breakout_fired = True

        return Signal(
            symbol=symbol,
            direction=SignalDirection.LONG,
            entry_price=entry,
            stop_price=st.or_low,
            target_price=target,
            strategy="orb",
            timeframe="5Min",
            metadata={
                "or_high": st.or_high,
                "or_low": st.or_low,
                "or_volume": st.or_volume,
                "breakout_volume": int(bar["volume"]),
            },
        )

    def reset(self) -> None:
        self.state.clear()

    # SignalGenerator compat -- ORB is event-driven via on_bar, not generate().
    # Accept and ignore extra kwargs (has_catalyst, symbol_trade_count, etc.)
    # that the scanner-driven loop in main.py passes to legacy strategies.
    def generate(self, symbol: str, bars, current_price: float, **_kwargs) -> Optional[Signal]:
        return None
