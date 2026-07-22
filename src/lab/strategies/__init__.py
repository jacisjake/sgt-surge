"""Lab strategy registry."""
from __future__ import annotations

from typing import Callable

from src.lab.protocol import Strategy
from src.lab.strategies.breakout_52w import Breakout52wStrategy

STRATEGY_REGISTRY: dict[str, Callable[[], Strategy]] = {
    "breakout_52w": Breakout52wStrategy,
}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGY_REGISTRY[name]()
    except KeyError as e:
        raise KeyError(f"Unknown strategy {name!r}; known: {sorted(STRATEGY_REGISTRY)}") from e
