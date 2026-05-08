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
