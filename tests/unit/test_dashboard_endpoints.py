from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.bot import web
from src.bot.signals.orb import _ORState


@pytest.fixture
def bot_app():
    bot = MagicMock()
    bot.client.is_authenticated = True
    bot.client.account_hash = "HASH-AAA"
    bot.client.get_account.return_value = {
        "equity": 270.0, "buying_power": 250.0, "cash": 250.0,
        "daytrade_count": 0, "is_pdt": False, "type": "CASH", "status": "active",
    }
    bot.config.schwab_app_key = "K"
    bot.config.trading_mode.value = "dry_run"
    bot.strategy.state = {
        "AAPL": _ORState(or_high=10.5, or_low=9.8, or_volume=6000,
                         or_locked=True, breakout_fired=False),
    }
    bot.position_manager.get_open_positions.return_value = []
    bot._scanner_results = []
    web.set_bot(bot)
    yield TestClient(web.app), bot


def test_auth_status(bot_app):
    client, _ = bot_app
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {
        "authenticated": True, "account_hash": "HASH-AAA", "broker": "schwab",
    }


def test_orb_state(bot_app):
    client, _ = bot_app
    r = client.get("/api/orb")
    assert r.status_code == 200
    payload = r.json()
    assert "AAPL" in payload
    assert payload["AAPL"]["or_high"] == 10.5
    assert payload["AAPL"]["or_locked"] is True


def test_status_returns_account_when_authenticated(bot_app):
    client, _ = bot_app
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["equity"] == 270.0
    assert body["trading_mode"] == "dry_run"


def test_status_returns_setup_mode_when_unauthenticated():
    bot = MagicMock()
    bot.client.is_authenticated = False
    web.set_bot(bot)
    r = TestClient(web.app).get("/api/status")
    assert r.status_code == 200
    assert r.json()["mode"] == "setup"


def test_dashboard_html_mentions_orb(bot_app):
    client, _ = bot_app
    r = client.get("/")
    assert r.status_code == 200
    assert "ORB" in r.text or "Opening Range" in r.text
