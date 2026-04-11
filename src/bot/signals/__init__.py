"""Signal generation strategies."""

from src.bot.signals.base import Signal, SignalDirection, SignalGenerator
from src.bot.signals.momentum_surge import MomentumSurgeStrategy

__all__ = [
    "Signal",
    "SignalDirection",
    "SignalGenerator",
    "MomentumSurgeStrategy",
]
