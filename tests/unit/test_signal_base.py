from datetime import datetime

import pytest

from src.bot.signals.base import Signal, SignalDirection


def test_signal_minimal_construction():
    s = Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.5,
        target_price=11.0,
        strategy="orb",
    )
    assert s.symbol == "AAPL"
    assert s.risk_amount == pytest.approx(0.5)
    assert s.risk_reward_ratio == pytest.approx(2.0)
    assert isinstance(s.timestamp, datetime)
    assert s.metadata == {}


def test_signal_long_validation():
    with pytest.raises(ValueError, match="Long stop must be below entry"):
        Signal(
            symbol="AAPL",
            direction=SignalDirection.LONG,
            entry_price=10.0,
            stop_price=10.5,
            target_price=11.0,
            strategy="orb",
        )


def test_signal_drops_strength_field():
    # Surge-era 'strength' attribute is removed
    s = Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.0,
        target_price=12.0,
        strategy="orb",
    )
    assert not hasattr(s, "strength")
    assert not hasattr(s, "strength_category")
    assert not hasattr(s, "has_catalyst")
