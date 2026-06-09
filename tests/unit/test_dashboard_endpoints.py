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


def test_bars_all_symbols_returns_closes_and_or_band(bot_app):
    client, bot = bot_app
    bot.stream_handler.get_close_series.return_value = {"AAPL": [10.0, 10.2, 10.1]}

    r = client.get("/api/bars")
    assert r.status_code == 200
    payload = r.json()
    assert payload["AAPL"]["closes"] == [10.0, 10.2, 10.1]
    assert payload["AAPL"]["or_high"] == 10.5
    assert payload["AAPL"]["or_low"] == 9.8
    assert payload["AAPL"]["fired"] is False
    # current = last close
    assert payload["AAPL"]["current"] == 10.1


def test_bars_single_symbol_returns_full_ohlcv(bot_app):
    client, bot = bot_app
    bot.stream_handler.get_ohlcv.return_value = [
        {"t": "2026-06-09T13:30:00+00:00", "o": 10.0, "h": 10.6, "l": 9.9, "c": 10.4, "v": 1000},
    ]

    r = client.get("/api/bars", params={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["or_high"] == 10.5
    assert body["bars"][0]["c"] == 10.4
    assert body["bars"][0]["v"] == 1000


def test_bars_symbol_with_empty_buffer_returns_empty(bot_app):
    client, bot = bot_app
    bot.stream_handler.get_close_series.return_value = {}  # nothing buffered yet

    r = client.get("/api/bars")
    assert r.status_code == 200
    payload = r.json()
    assert payload["AAPL"]["closes"] == []
    assert payload["AAPL"]["current"] is None
