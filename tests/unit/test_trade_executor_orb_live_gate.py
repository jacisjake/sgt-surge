"""Capital-safety gate: ENABLE_ORB_LIVE blocks real ORB entries under live mode."""

from unittest.mock import MagicMock

import pytest

from config.settings import TradingMode
from src.bot.executor import TradeExecutor
from src.bot.processor import TradeParams
from src.bot.signals.base import Signal, SignalDirection
from src.core.order_executor import OrderExecutor, OrderResult, OrderStatus
from src.core.position_manager import PositionSide


def _make_trade_params(symbol: str = "TEST") -> TradeParams:
    signal = Signal(
        symbol=symbol,
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.0,
        target_price=12.0,
        strategy="orb",
    )
    return TradeParams(
        symbol=symbol,
        side="buy",
        quantity=10,
        entry_price=10.0,
        stop_price=9.0,
        target_price=12.0,
        order_type="market",
        time_in_force="day",
        signal=signal,
    )


@pytest.mark.asyncio
async def test_live_mode_blocks_entry_when_enable_orb_live_false():
    schwab = MagicMock()
    schwab.is_authenticated = True
    order_ex = OrderExecutor(client=schwab, trading_mode=TradingMode.LIVE)
    order_ex.execute_market_order = MagicMock()

    pos_mgr = MagicMock()
    pos_mgr.has_position.return_value = False

    executor = TradeExecutor(
        order_executor=order_ex,
        position_manager=pos_mgr,
        enable_orb_live=False,
    )

    result = await executor.execute_entry(_make_trade_params())

    assert result.success is False
    assert "ENABLE_ORB_LIVE" in (result.error or "")
    order_ex.execute_market_order.assert_not_called()
    schwab.submit_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_live_mode_allows_entry_when_enable_orb_live_true():
    schwab = MagicMock()
    schwab.is_authenticated = True
    order_ex = OrderExecutor(client=schwab, trading_mode=TradingMode.LIVE)
    order_ex.execute_market_order = MagicMock(
        return_value=OrderResult(
            success=True,
            order_id="ORD-1",
            status=OrderStatus.FILLED,
            filled_qty=10,
            filled_price=10.0,
        )
    )
    order_ex.execute_stop_limit_order = MagicMock(
        return_value=OrderResult(
            success=True, order_id="STOP-1", status=OrderStatus.SUBMITTED
        )
    )

    pos = MagicMock()
    pos.stop_loss = 9.0
    pos.symbol = "TEST"
    pos.qty = 10
    pos.side = PositionSide.LONG

    pos_mgr = MagicMock()
    pos_mgr.has_position.return_value = False
    pos_mgr.open_position.return_value = pos

    executor = TradeExecutor(
        order_executor=order_ex,
        position_manager=pos_mgr,
        enable_orb_live=True,
    )

    result = await executor.execute_entry(_make_trade_params())

    assert result.success is True
    order_ex.execute_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_dry_run_still_reaches_order_executor_when_gate_off():
    """dry_run path is allowed through TradeExecutor; OrderExecutor fabricates fills."""
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.get_latest_price.return_value = 10.0
    order_ex = OrderExecutor(client=schwab, trading_mode=TradingMode.DRY_RUN)

    pos = MagicMock()
    pos.stop_loss = None
    pos.symbol = "TEST"
    pos.qty = 10
    pos.side = PositionSide.LONG

    pos_mgr = MagicMock()
    pos_mgr.has_position.return_value = False
    pos_mgr.open_position.return_value = pos

    executor = TradeExecutor(
        order_executor=order_ex,
        position_manager=pos_mgr,
        enable_orb_live=False,
    )

    result = await executor.execute_entry(_make_trade_params())

    assert result.success is True
    assert result.order_result is not None
    assert result.order_result.dry_run is True
    schwab.submit_market_order.assert_not_called()
