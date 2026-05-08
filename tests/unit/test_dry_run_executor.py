from unittest.mock import MagicMock

from config.settings import TradingMode
from src.core.order_executor import OrderExecutor


def test_dry_run_market_buy_does_not_call_place_order():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.get_latest_price.return_value = 10.50

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.DRY_RUN)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="buy")

    assert result.success is True
    assert result.filled_qty == 5
    assert result.filled_price == 10.50
    assert getattr(result, "dry_run", False) is True
    schwab.submit_market_order.assert_not_called()


def test_dry_run_market_sell_uses_current_quote_for_exit_fill():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.get_latest_price.return_value = 12.00

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.DRY_RUN)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="sell")

    assert result.success is True
    assert result.filled_price == 12.00
    schwab.submit_market_order.assert_not_called()


def test_live_mode_calls_place_order():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.submit_market_order.return_value = "ORD-1"
    schwab.get_order.return_value = {
        "id": "ORD-1", "status": "filled", "qty": 5, "filled_qty": 5,
        "type": "market", "price": None, "stop_price": None,
        "submitted_at": "2026-05-08T13:30:00+0000", "symbol": "AAPL",
    }

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.LIVE)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="buy")

    assert schwab.submit_market_order.called
    assert result.success is True
