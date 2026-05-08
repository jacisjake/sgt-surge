from unittest.mock import MagicMock

import pytest

from src.bot.config import BotConfig
from src.bot.processor import SignalProcessor
from src.bot.signals.base import Signal, SignalDirection


def make_signal() -> Signal:
    return Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.35,  # 6.5% risk (under 7% max)
        target_price=12.0,
        strategy="orb",
    )


def test_processor_passes_when_limits_ok():
    config = BotConfig()
    portfolio_limits = MagicMock()
    portfolio_limits.check_can_open_position.return_value = MagicMock(
        passed=True, message=None, action=None
    )
    position_sizer = MagicMock()
    position_sizer.calculate_momentum_size.return_value = MagicMock(
        shares=10, capped_by_buying_power=False, capped_by_max_position=False
    )

    proc = SignalProcessor(
        config=config,
        position_sizer=position_sizer,
        portfolio_limits=portfolio_limits,
    )
    result = proc.process(
        signal=make_signal(),
        account_equity=270.0,
        buying_power=270.0,
        current_positions=0,
        daytrade_count=0,
    )
    assert result.passed
    assert result.trade_params.quantity == 10


def test_processor_rejects_when_portfolio_limit_blocks():
    config = BotConfig()
    portfolio_limits = MagicMock()
    portfolio_limits.check_can_open_position.return_value = MagicMock(
        passed=False, message="Daily loss limit hit", action=None
    )
    position_sizer = MagicMock()

    proc = SignalProcessor(
        config=config,
        position_sizer=position_sizer,
        portfolio_limits=portfolio_limits,
    )
    result = proc.process(
        signal=make_signal(),
        account_equity=270.0,
        buying_power=270.0,
        current_positions=0,
        daytrade_count=0,
    )
    assert not result.passed
    assert "Daily loss limit" in result.rejection_reason


def test_processor_takes_no_regime_argument():
    """Regime gate has been removed."""
    import inspect

    sig = inspect.signature(SignalProcessor.__init__)
    assert "regime_detector" not in sig.parameters
