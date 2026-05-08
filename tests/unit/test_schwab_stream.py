import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.schwab_stream import SchwabStreamClient


@pytest.fixture
def mock_stream_client_class():
    sc = AsyncMock()
    sc.login = AsyncMock(return_value=None)
    sc.handle_message = AsyncMock()
    sc.add_chart_equity_handler = MagicMock()
    sc.add_level_one_equity_handler = MagicMock()
    sc.add_account_activity_handler = MagicMock()
    sc.chart_equity_subs = AsyncMock()
    sc.level_one_equity_subs = AsyncMock()
    sc.account_activity_sub = AsyncMock()
    sc.chart_equity_unsubs = AsyncMock()
    sc.level_one_equity_unsubs = AsyncMock()
    return sc


@pytest.fixture
def stream(mock_stream_client_class):
    schwab_client = MagicMock()
    schwab_client._client = MagicMock()
    with patch("src.core.schwab_stream.StreamClient", return_value=mock_stream_client_class):
        s = SchwabStreamClient(schwab_client=schwab_client)
        s._stream = mock_stream_client_class
        yield s


def test_callbacks_register(stream):
    bar_cb = lambda b: None
    quote_cb = lambda q: None
    trade_cb = lambda t: None
    stream.on_bar(bar_cb)
    stream.on_quote(quote_cb)
    stream.on_trade_update(trade_cb)
    assert bar_cb in stream._bar_callbacks
    assert quote_cb in stream._quote_callbacks
    assert trade_cb in stream._trade_callbacks


@pytest.mark.asyncio
async def test_connect_data_logs_in_and_registers_handlers(stream, mock_stream_client_class):
    ok = await stream.connect_data()
    assert ok is True
    mock_stream_client_class.login.assert_awaited_once()
    mock_stream_client_class.add_chart_equity_handler.assert_called_once()
    mock_stream_client_class.add_level_one_equity_handler.assert_called_once()


@pytest.mark.asyncio
async def test_subscribe_calls_chart_and_quote_subs(stream, mock_stream_client_class):
    await stream.connect_data()
    await stream.subscribe(bars=["AAPL", "MSFT"], quotes=["AAPL"])
    mock_stream_client_class.chart_equity_subs.assert_awaited_once_with(["AAPL", "MSFT"])
    mock_stream_client_class.level_one_equity_subs.assert_awaited_once_with(["AAPL"])
    assert "AAPL" in stream.subscribed_symbols["bars"]
