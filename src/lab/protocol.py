"""Shared strategy protocol for backtest / paper / live (decisions only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

import pandas as pd


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class OrderIntent:
    """Strategy decision — never a broker call.

    Sizing contract (v1):
      - Exits (SELL): strategy sets qty (and optional notional for paper).
      - Entries (BUY): strategy sets risk_pct + stop_price; runner applies cash caps.
    """

    symbol: str
    side: Side
    reason: str
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_pct: Optional[float] = None
    qty: Optional[float] = None
    notional: Optional[float] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class PositionView:
    """Normalized open position seen by Strategy.plan."""

    symbol: str
    qty: float
    avg_entry_price: float
    entry_date: date
    stop_price: Optional[float] = None
    notional: Optional[float] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class PortfolioView:
    as_of: date
    equity: float
    available_cash: float
    positions: list[PositionView]


@dataclass
class MarketContext:
    """Bars + optional cross-asset series (e.g. SPY for regime)."""

    bars_by_symbol: dict[str, pd.DataFrame]
    extras: dict[str, pd.DataFrame] = field(default_factory=dict)
    now: date = field(default_factory=date.today)


@runtime_checkable
class Strategy(Protocol):
    name: str

    def plan(
        self,
        portfolio: PortfolioView,
        market: MarketContext,
        params: dict[str, Any],
    ) -> list[OrderIntent]:
        """Pure: no I/O, no broker. Prefer exits before entries.

        Runner still enforces SELL-before-BUY regardless of list order.
        """
        ...


def bar_index_for_date(df: pd.DataFrame, as_of: date) -> int | None:
    """Return positional index of *as_of* in a DatetimeIndex frame, or None."""
    for i, ts in enumerate(df.index):
        if ts.date() == as_of:
            return i
    return None


def order_exits_before_entries(intents: list[OrderIntent]) -> list[OrderIntent]:
    sells = [i for i in intents if i.side == Side.SELL]
    buys = [i for i in intents if i.side == Side.BUY]
    other = [i for i in intents if i.side not in (Side.SELL, Side.BUY)]
    return sells + buys + other
