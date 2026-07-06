"""
Base classes for signal generation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import pandas as pd


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""

    symbol: str
    direction: SignalDirection
    entry_price: float
    stop_price: float
    target_price: float
    strategy: str
    timestamp: datetime = field(default_factory=datetime.now)
    timeframe: str = "5Min"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.direction == SignalDirection.LONG:
            if self.stop_price >= self.entry_price:
                raise ValueError("Long stop must be below entry")
            if self.target_price <= self.entry_price:
                raise ValueError("Long target must be above entry")
        else:
            if self.stop_price <= self.entry_price:
                raise ValueError("Short stop must be above entry")
            if self.target_price >= self.entry_price:
                raise ValueError("Short target must be below entry")

    @property
    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_amount(self) -> float:
        return abs(self.target_price - self.entry_price)

    @property
    def risk_reward_ratio(self) -> float:
        return self.reward_amount / self.risk_amount

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "risk_amount": self.risk_amount,
            "risk_reward": self.risk_reward_ratio,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class SignalGenerator(ABC):
    """Base class strategies inherit from."""

    @abstractmethod
    def generate(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_price: float,
    ) -> Optional[Signal]:
        ...
