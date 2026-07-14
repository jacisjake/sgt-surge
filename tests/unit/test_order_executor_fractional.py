"""Fractional-share support in the order executor.

Whether Schwab's Trader API actually accepts a fractional quantity is unproven —
this only governs what the executor SENDS. Default stays whole-share (current,
safe behavior); allow_fractional=True lets a sub-1-share quantity through so we
can test the API empirically with a tiny real order.
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
