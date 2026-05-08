from unittest.mock import MagicMock, patch

import pytest

from src.core.schwab_client import SchwabClient


@pytest.fixture
def schwab(mock_schwab_py_client):
    with patch("src.core.schwab_client.easy_client", return_value=mock_schwab_py_client):
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
    with patch("src.core.schwab_client.easy_client", return_value=mock_schwab_py_client):
        client = SchwabClient(
            app_key="K", app_secret="S",
            callback_url="https://x/y", token_path="/tmp/t.json",
            pinned_account_hash="HASH-BBB",
        )
        assert client.account_hash == "HASH-BBB"


def test_unauthenticated_when_token_missing(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no token")

    monkeypatch.setattr("src.core.schwab_client.easy_client", boom)
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
