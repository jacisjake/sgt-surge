"""Fractional-share support in the order executor.

Fractional is the default: on a small account, sizing lands well below one
share of most names, so a whole-share default meant the bot rejected its own
stop orders locally. allow_fractional=False is the opt-out for whole-lot
callers.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from config.settings import TradingMode
from src.core.order_executor import OrderExecutor


def _executor(allow_fractional=False):
    client = MagicMock()
    client.submit_market_order.return_value = "ORDER123"
    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)
    ex.allow_fractional = allow_fractional
    return ex, client


# ── whole-share (default, unchanged) ─────────────────────────────────────

def test_default_rounds_down_to_whole_shares():
    ex, client = _executor(allow_fractional=False)
    ex._submit_order(order_type="market", symbol="AAA", qty=3.9, side="buy")
    client.submit_market_order.assert_called_once_with("AAA", 3, "buy")


def test_default_rejects_sub_one_share():
    ex, _ = _executor(allow_fractional=False)
    try:
        ex._submit_order(order_type="market", symbol="AAA", qty=0.1, side="buy")
        assert False, "expected ValueError for <1 whole share"
    except ValueError:
        pass


# ── fractional (opt-in) ──────────────────────────────────────────────────

def test_fractional_preserves_sub_one_quantity():
    ex, client = _executor(allow_fractional=True)
    ex._submit_order(order_type="market", symbol="AAA", qty=0.1, side="buy")
    client.submit_market_order.assert_called_once_with("AAA", 0.1, "buy")


def test_fractional_rounds_to_four_decimals():
    ex, client = _executor(allow_fractional=True)
    ex._submit_order(order_type="market", symbol="AAA", qty=0.123456, side="buy")
    client.submit_market_order.assert_called_once_with("AAA", 0.1235, "buy")


def test_fractional_still_rejects_zero():
    ex, _ = _executor(allow_fractional=True)
    try:
        ex._submit_order(order_type="market", symbol="AAA", qty=0.0, side="buy")
        assert False, "expected ValueError for zero quantity"
    except ValueError:
        pass


def test_fractional_allows_whole_shares_too():
    ex, client = _executor(allow_fractional=True)
    ex._submit_order(order_type="market", symbol="AAA", qty=2.0, side="buy")
    client.submit_market_order.assert_called_once_with("AAA", 2.0, "buy")


# ── constructor default: fractional is the norm, not an opt-in ───────────

def test_stop_limit_on_a_fractional_position_reaches_the_client_by_default():
    """The bot builds its executor at src/bot/main.py:78 without touching the
    flag, so _place_broker_stop rejected every fractional position locally
    ("Cannot buy less than 1 share") before Schwab ever saw a stop order.
    """
    client = MagicMock()
    client.submit_stop_limit_order.return_value = "STOP123"
    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)  # as the bot constructs it

    ex._submit_order(
        order_type="stop_limit", symbol="JPM", qty=0.0717, side="sell",
        stop_price=347.63, limit_price=347.53,
    )

    client.submit_stop_limit_order.assert_called_once_with(
        "JPM", 0.0717, "sell", 347.63, 347.53
    )


def test_allow_fractional_is_a_constructor_parameter():
    """Settable at construction so a caller cannot silently inherit the wrong
    mode by forgetting a post-construction assignment."""
    ex = OrderExecutor(MagicMock(), trading_mode=TradingMode.LIVE,
                       allow_fractional=False)
    assert ex.allow_fractional is False
