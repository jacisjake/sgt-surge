from unittest.mock import MagicMock, patch

import pytest

from src.core.schwab_client import SchwabClient


@pytest.fixture
def schwab(mock_schwab_py_client):
    with patch("src.core.schwab_client.client_from_token_file", return_value=mock_schwab_py_client):
        client = SchwabClient(
            app_key="K", app_secret="S",
            callback_url="https://ut.gitsum.rest/schwab/oauth/callback",
            token_path="/tmp/token.json",
        )
        client._client = mock_schwab_py_client
        yield client


def test_authenticated_after_construction(schwab):
    assert schwab.is_authenticated is True


def test_account_hash_resolved_from_first_account(schwab):
    assert schwab.account_hash == "HASH-AAA"


def test_account_hash_pinned_via_constructor(mock_schwab_py_client):
    with patch("src.core.schwab_client.client_from_token_file", return_value=mock_schwab_py_client):
        client = SchwabClient(
            app_key="K", app_secret="S",
            callback_url="https://x/y", token_path="/tmp/t.json",
            pinned_account_hash="HASH-BBB",
        )
        assert client.account_hash == "HASH-BBB"


def test_unauthenticated_when_token_missing(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no token")

    monkeypatch.setattr("src.core.schwab_client.client_from_token_file", boom)
    client = SchwabClient(
        app_key="K", app_secret="S",
        callback_url="https://x/y", token_path="/tmp/missing.json",
    )
    assert client.is_authenticated is False
    assert client.account_hash is None


def test_get_account_returns_normalized_dict(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {
                    "liquidationValue": 270.0,
                    "cashAvailableForTrading": 250.0,
                    "buyingPower": 250.0,
                },
                "isDayTrader": False,
                "roundTrips": 1,
                "type": "CASH",
                "positions": [],
            }
        },
    )

    out = schwab.get_account()
    assert out["equity"] == 270.0
    assert out["buying_power"] == 250.0
    assert out["cash"] == 250.0
    assert out["daytrade_count"] == 1
    assert out["status"] == "active"


def test_get_account_cash_account_buying_power_falls_back_to_cash(schwab, mock_schwab_py_client):
    # Cash accounts have no buyingPower field; Schwab only returns
    # cashAvailableForTrading. Ensure we surface that as buying_power so the
    # sizer doesn't see BP=0 and refuse every trade.
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {
                    "liquidationValue": 198.04,
                    "cashAvailableForTrading": 198.04,
                    "cashBalance": 198.04,
                    # NO buyingPower key — matches Schwab's real cash-account payload
                },
                "isDayTrader": False,
                "roundTrips": 0,
                "type": "CASH",
                "positions": [],
            }
        },
    )

    out = schwab.get_account()
    assert out["buying_power"] == 198.04
    assert out["cash"] == 198.04
    assert out["equity"] == 198.04
    assert out["type"] == "CASH"


def test_get_buying_power_and_equity(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {
                    "liquidationValue": 270.0,
                    "buyingPower": 250.0,
                    "cashAvailableForTrading": 250.0,
                },
                "isDayTrader": False,
                "roundTrips": 0,
                "type": "CASH",
                "positions": [],
            }
        },
    )
    assert schwab.get_buying_power() == 250.0
    assert schwab.get_equity() == 270.0


def test_get_positions(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {"liquidationValue": 270.0, "buyingPower": 250.0,
                                     "cashAvailableForTrading": 250.0},
                "isDayTrader": False, "roundTrips": 0, "type": "CASH",
                "positions": [
                    {
                        "instrument": {"symbol": "AAPL"},
                        "longQuantity": 5.0,
                        "shortQuantity": 0.0,
                        "averagePrice": 10.0,
                        "marketValue": 55.0,
                        "currentDayProfitLoss": 5.0,
                        "currentDayProfitLossPercentage": 10.0,
                    }
                ],
            }
        },
    )

    positions = schwab.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "AAPL"
    assert p["qty"] == 5.0
    assert p["avg_entry_price"] == 10.0
    assert p["current_price"] == pytest.approx(11.0)
    assert p["unrealized_pl"] == 5.0


def test_get_bars_5min(schwab, mock_schwab_py_client):
    candles = [
        {"datetime": 1715170200000, "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4, "volume": 1000},
        {"datetime": 1715170500000, "open": 10.4, "high": 10.7, "low": 10.3, "close": 10.6, "volume": 1500},
    ]
    mock_schwab_py_client.get_price_history_every_five_minutes.return_value = MagicMock(
        status_code=200,
        json=lambda: {"candles": candles, "symbol": "AAPL", "empty": False},
    )

    bars = schwab.get_bars("AAPL", timeframe="5Min", limit=2)
    assert len(bars) == 2
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert bars["close"].iloc[-1] == 10.6


def test_get_latest_price(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_quote.return_value = MagicMock(
        status_code=200,
        json=lambda: {"AAPL": {"quote": {"lastPrice": 10.55}}},
    )
    assert schwab.get_latest_price("AAPL") == 10.55


def test_get_latest_quotes_multi_symbol(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_quotes.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "AAPL": {"quote": {"lastPrice": 10.55, "bidPrice": 10.5, "askPrice": 10.6,
                               "netChange": 0.5, "netPercentChangeInDouble": 5.0}},
            "MSFT": {"quote": {"lastPrice": 20.0, "bidPrice": 19.9, "askPrice": 20.1,
                               "netChange": 1.0, "netPercentChangeInDouble": 5.0}},
        },
    )
    quotes = schwab.get_latest_quotes_with_change(["AAPL", "MSFT"])
    assert quotes["AAPL"]["price"] == 10.55
    assert quotes["AAPL"]["change_pct"] == 5.0
    assert quotes["MSFT"]["bid"] == 19.9


def test_submit_market_order_calls_place_order_with_account_hash(schwab, mock_schwab_py_client):
    mock_schwab_py_client.place_order.return_value = MagicMock(
        status_code=201,
        headers={"Location": "https://api.schwabapi.com/.../orders/9876"},
    )

    order_id = schwab.submit_market_order("AAPL", qty=5, side="buy")
    assert order_id == "9876"

    args, kwargs = mock_schwab_py_client.place_order.call_args
    assert args[0] == "HASH-AAA"


def test_submit_stop_limit_order(schwab, mock_schwab_py_client):
    mock_schwab_py_client.place_order.return_value = MagicMock(
        status_code=201,
        headers={"Location": "https://api.schwabapi.com/.../orders/4321"},
    )

    order_id = schwab.submit_stop_limit_order(
        "AAPL", qty=5, side="sell", stop_price=9.0, limit_price=8.95
    )
    assert order_id == "4321"


def test_cancel_order(schwab, mock_schwab_py_client):
    mock_schwab_py_client.cancel_order.return_value = MagicMock(status_code=200)
    assert schwab.cancel_order("9876") is True
    mock_schwab_py_client.cancel_order.assert_called_once_with("9876", "HASH-AAA")


def test_get_orders_normalizes(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_orders_for_account.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {
                "orderId": 1234,
                "status": "FILLED",
                "filledQuantity": 5,
                "orderLegCollection": [{"instrument": {"symbol": "AAPL"}, "quantity": 5}],
                "orderType": "MARKET",
                "price": None,
                "stopPrice": None,
                "enteredTime": "2026-05-08T13:30:00+0000",
            },
            {
                "orderId": 1235,
                "status": "WORKING",
                "filledQuantity": 0,
                "orderLegCollection": [{"instrument": {"symbol": "AAPL"}, "quantity": 5}],
                "orderType": "STOP_LIMIT",
                "price": 9.9,
                "stopPrice": 9.95,
                "enteredTime": "2026-05-08T13:31:00+0000",
            },
        ],
    )

    orders = schwab.get_orders(status="open")
    assert len(orders) == 1
    assert orders[0]["id"] == "1235"
    assert orders[0]["type"] == "stop_limit"
    assert orders[0]["stop_price"] == 9.95


def test_get_orders_status_all(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_orders_for_account.return_value = MagicMock(
        status_code=200,
        json=lambda: [{"orderId": 1, "status": "FILLED", "filledQuantity": 5,
                       "orderLegCollection": [{"instrument": {"symbol": "X"}, "quantity": 5}],
                       "orderType": "MARKET", "price": None, "stopPrice": None,
                       "enteredTime": "2026-05-08T13:30:00+0000"}],
    )
    assert len(schwab.get_orders(status="all")) == 1
