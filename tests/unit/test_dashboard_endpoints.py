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
    bot.config.enable_orb_live = False
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


def test_open_orders_returns_broker_orders(bot_app):
    client, bot = bot_app
    bot.client.get_orders.return_value = [
        {"id": "1007178842794", "symbol": "GS", "qty": 0.0218, "filled_qty": 0.0,
         "type": "market", "status": "pending_activation", "submitted_at": "2026-07-14T23:31:12+0000"},
    ]
    r = client.get("/api/orders")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["symbol"] == "GS"
    assert body[0]["qty"] == 0.0218
    assert body[0]["status"] == "pending_activation"


def test_open_orders_empty_when_unauthenticated():
    bot = MagicMock()
    bot.client.is_authenticated = False
    web.set_bot(bot)
    assert TestClient(web.app).get("/api/orders").json() == []


def test_status_returns_account_when_authenticated(bot_app):
    client, _ = bot_app
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["equity"] == 270.0
    assert body["trading_mode"] == "dry_run"
    assert body["enable_orb_live"] is False


def test_status_serves_cached_account_without_broker_call(bot_app):
    client, bot = bot_app
    bot._account_snapshot = {
        "equity": 195.0, "buying_power": 1.6, "cash": 1.6,
        "daytrade_count": 0, "is_pdt": False, "type": "CASH", "status": "active",
        "_raw_positions": [],
    }
    r = client.get("/api/status")
    assert r.json()["account"]["equity"] == 195.0
    bot.client.get_account.assert_not_called()


def test_open_orders_serves_cached_orders_without_broker_call(bot_app):
    client, bot = bot_app
    bot._open_orders_snapshot = [
        {"symbol": "PGEN", "qty": 0.215, "status": "pending_activation"},
    ]
    r = client.get("/api/orders")
    assert r.json()[0]["symbol"] == "PGEN"
    bot.client.get_orders.assert_not_called()


def test_status_returns_setup_mode_when_unauthenticated():
    bot = MagicMock()
    bot.client.is_authenticated = False
    web.set_bot(bot)
    r = TestClient(web.app).get("/api/status")
    assert r.status_code == 200
    assert r.json()["mode"] == "setup"


def test_dashboard_html_is_live_ops_not_paper(bot_app):
    client, _ = bot_app
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "#04040b" in html
    assert 'id="k-eq"' in html and 'id="k-day"' in html
    assert 'id="book-table"' in html
    assert 'id="swing-table"' in html
    assert 'id="scanner"' in html
    assert 'id="tape"' in html
    assert "Live <em>book</em>" not in html
    assert 'id="paper-scoreboard"' not in html
    assert "Lab paper" not in html
    assert 'id="orb-table"' not in html
    assert "ORB money" in html



def test_paper_page_is_gone(bot_app):
    """The paper surface was removed — one system only."""
    client, _ = bot_app
    assert client.get("/paper").status_code == 404



def test_education_endpoint_missing_file(bot_app, tmp_path):
    client, bot = bot_app
    bot.config.state_dir = str(tmp_path)
    r = client.get("/api/education")
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_ops_endpoint_reads_last_live_run(bot_app, tmp_path):
    client, bot = bot_app
    bot.config.state_dir = str(tmp_path)
    (tmp_path / "live_swing_last.json").write_text(
        '{"date":"2026-08-18","preview":false,"plan":[{"symbol":"ATAI","action":"buy"}]}'
    )
    (tmp_path / "universes").mkdir()
    (tmp_path / "universes" / "live.txt").write_text("ATAI ET NVDA\n")
    body = client.get("/api/ops").json()
    assert body["last_live_swing"]["plan"][0]["symbol"] == "ATAI"
    assert body["universe"]["n"] == 3



def test_education_endpoint_reads_brief(bot_app, tmp_path):
    client, bot = bot_app
    bot.config.state_dir = str(tmp_path)
    d = tmp_path / "lab" / "conditions"
    d.mkdir(parents=True)
    (d / "2026-07-22.json").write_text(
        __import__("json").dumps(
            {
                "condition": {
                    "as_of": "2026-07-22",
                    "tags": ["risk_off"],
                    "confidence": "high",
                    "summary": "Risk-off test.",
                    "evidence": {},
                },
                "education": {"primary": {"id": "risk_off_prep", "title": "Risk-off", "plays": []}},
                "lab_actions": [],
            }
        )
    )
    body = client.get("/api/education").json()
    assert body["exists"] is True
    assert body["condition"]["tags"] == ["risk_off"]


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


def test_compare_returns_orb_edge_metrics(bot_app, tmp_path):
    client, bot = bot_app
    # ORB live ledger: one +10% winner, one -5% loser.
    bot.trade_ledger.get_trades.return_value = [
        {"symbol": "AAPL", "entry_price": 10.0, "exit_price": 11.0},
        {"symbol": "MSFT", "entry_price": 20.0, "exit_price": 19.0},
    ]
    bot.trade_ledger.get_total_realized_pnl.return_value = 3.21
    bot.config.state_dir = str(tmp_path)

    r = client.get("/api/compare")
    assert r.status_code == 200
    body = r.json()

    assert body["orb"]["n_closed"] == 2
    assert body["orb"]["win_rate"] == 0.5
    assert body["orb"]["realized_pnl"] == 3.21
    assert body["orb"]["account_equity"] == 270.0  # from get_account mock
    assert "paper" not in body


def test_compare_handles_empty_ledger(bot_app, tmp_path):
    client, bot = bot_app
    bot.trade_ledger.get_trades.return_value = []
    bot.trade_ledger.get_total_realized_pnl.return_value = 0.0
    bot.config.state_dir = str(tmp_path)

    body = client.get("/api/compare").json()
    assert body["orb"]["n_closed"] == 0


def test_paper_api_is_gone(bot_app, tmp_path):
    client, bot = bot_app
    bot.config.state_dir = str(tmp_path)
    assert client.get("/api/paper").status_code == 404


def test_bars_symbol_with_empty_buffer_returns_empty(bot_app):
    client, bot = bot_app
    bot.stream_handler.get_close_series.return_value = {}  # nothing buffered yet

    r = client.get("/api/bars")
    assert r.status_code == 200
    payload = r.json()
    assert payload["AAPL"]["closes"] == []
    assert payload["AAPL"]["current"] is None


def test_dashboard_shows_position_value_and_total(bot_app):
    """Per-position market value + a deployed KPI.

    Eight ~$25 positions summing to the whole account is invisible when the
    table only shows qty and price; the total is what makes it obvious.
    """
    client, _ = bot_app
    r = client.get("/")
    assert r.status_code == 200
    assert "<th>Value</th>" in r.text
    assert 'id="k-deployed"' in r.text
    assert 'id="k-lots"' in r.text
