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


def test_status_returns_setup_mode_when_unauthenticated():
    bot = MagicMock()
    bot.client.is_authenticated = False
    web.set_bot(bot)
    r = TestClient(web.app).get("/api/status")
    assert r.status_code == 200
    assert r.json()["mode"] == "setup"


def test_dashboard_html_is_breakout_centric(bot_app):
    client, _ = bot_app
    r = client.get("/")
    assert r.status_code == 200
    # breakout_52w is now the live strategy; the retired ORB table is gone.
    assert "breakout_52w" in r.text
    assert "Live positions" in r.text and "Open orders" in r.text
    assert 'id="orb-table"' not in r.text


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


def test_paper_forward_reads_ledger_and_computes_stats(bot_app, tmp_path):
    client, bot = bot_app
    ledger = {
        "starting_equity": 200.0, "realized_pnl": 12.5, "last_date": "2026-06-15",
        "open_positions": [{"symbol": "TGT", "entry_date": "2026-06-12",
                            "entry_price": 135.0, "stop_price": 124.0, "notional": 25.0}],
        "closed_trades": [{"symbol": "IWM", "entry_date": "2026-06-10",
                           "exit_date": "2026-06-13", "entry_price": 290.0,
                           "exit_price": 305.0, "pnl": 12.5, "reason": "trend_break"}],
    }
    (tmp_path / "swing_paper_breakout.json").write_text(__import__("json").dumps(ledger))
    bot.config.state_dir = str(tmp_path)

    r = client.get("/api/paper")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["equity"] == 212.5
    assert round(body["total_return"], 5) == round(12.5 / 200, 5)
    assert body["n_open"] == 1 and body["n_closed"] == 1
    assert body["win_rate"] == 1.0
    assert body["open_positions"][0]["symbol"] == "TGT"
    assert body["closed_trades"][0]["reason"] == "trend_break"


def test_compare_returns_orb_and_paper_edge_metrics(bot_app, tmp_path):
    client, bot = bot_app
    # ORB live ledger: one +10% winner, one -5% loser.
    bot.trade_ledger.get_trades.return_value = [
        {"symbol": "AAPL", "entry_price": 10.0, "exit_price": 11.0},
        {"symbol": "MSFT", "entry_price": 20.0, "exit_price": 19.0},
    ]
    bot.trade_ledger.get_total_realized_pnl.return_value = 3.21
    # Paper breakout_52w ledger: two closed trades.
    ledger = {
        "starting_equity": 200.0, "realized_pnl": -6.41,
        "open_positions": [],
        "closed_trades": [
            {"symbol": "AMD", "entry_price": 100.0, "exit_price": 92.0, "pnl": -2.1, "reason": "stop"},
            {"symbol": "GS", "entry_price": 100.0, "exit_price": 110.0, "pnl": 10.0, "reason": "trend_break"},
        ],
    }
    (tmp_path / "swing_paper_breakout.json").write_text(__import__("json").dumps(ledger))
    bot.config.state_dir = str(tmp_path)

    r = client.get("/api/compare")
    assert r.status_code == 200
    body = r.json()

    assert body["orb"]["n_closed"] == 2
    assert body["orb"]["win_rate"] == 0.5
    assert body["orb"]["realized_pnl"] == 3.21
    assert body["orb"]["account_equity"] == 270.0  # from get_account mock

    assert body["paper"]["n_closed"] == 2
    assert body["paper"]["win_rate"] == 0.5
    assert body["paper"]["realized_pnl"] == -6.41


def test_compare_handles_empty_ledgers(bot_app, tmp_path):
    client, bot = bot_app
    bot.trade_ledger.get_trades.return_value = []
    bot.trade_ledger.get_total_realized_pnl.return_value = 0.0
    bot.config.state_dir = str(tmp_path)  # no paper ledger file

    body = client.get("/api/compare").json()
    assert body["orb"]["n_closed"] == 0
    assert body["paper"]["n_closed"] == 0


def test_paper_forward_missing_ledger_returns_not_exists(bot_app, tmp_path):
    client, bot = bot_app
    bot.config.state_dir = str(tmp_path)  # no ledger file present
    assert client.get("/api/paper").json() == {"exists": False}


def test_bars_symbol_with_empty_buffer_returns_empty(bot_app):
    client, bot = bot_app
    bot.stream_handler.get_close_series.return_value = {}  # nothing buffered yet

    r = client.get("/api/bars")
    assert r.status_code == 200
    payload = r.json()
    assert payload["AAPL"]["closes"] == []
    assert payload["AAPL"]["current"] is None
