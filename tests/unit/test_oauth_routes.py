from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bot import web


@pytest.fixture
def client_with_bot():
    bot = MagicMock()
    bot.config.schwab_app_key = "K"
    bot.config.schwab_oauth_redirect_uri = "https://ut.gitsum.rest/schwab/oauth/callback"
    bot.config.schwab_app_secret = "S"
    bot.config.schwab_token_path = "/tmp/token.json"
    bot.client.is_authenticated = False
    bot.client.account_hash = None
    web.set_bot(bot)
    web._pending_auth_contexts.clear()
    yield TestClient(web.app), bot
    web._pending_auth_contexts.clear()


def test_oauth_start_redirects_to_schwab_authorize(client_with_bot):
    client, _ = client_with_bot
    fake_ctx = SimpleNamespace(
        state="STATE123",
        callback_url="https://ut.gitsum.rest/schwab/oauth/callback",
        authorization_url=(
            "https://api.schwabapi.com/v1/oauth/authorize"
            "?response_type=code&client_id=K"
            "&redirect_uri=https%3A%2F%2Fut.gitsum.rest%2Fschwab%2Foauth%2Fcallback"
            "&state=STATE123"
        ),
    )
    with patch("src.bot.web.get_auth_context", return_value=fake_ctx):
        resp = client.get("/schwab/oauth/start", follow_redirects=False)
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://api.schwabapi.com/v1/oauth/authorize")
    assert "client_id=K" in location
    assert "redirect_uri=https" in location
    assert web._pending_auth_contexts["STATE123"] is fake_ctx


def test_oauth_callback_persists_token_and_reloads_client(client_with_bot):
    client, bot = client_with_bot
    fake_ctx = SimpleNamespace(state="STATE123", callback_url="x")
    web._pending_auth_contexts["STATE123"] = fake_ctx
    with patch("src.bot.web.client_from_received_url") as cfru:
        cfru.return_value = MagicMock()
        resp = client.get(
            "/schwab/oauth/callback",
            params={"code": "ABC", "state": "STATE123"},
            follow_redirects=False,
        )
    assert resp.status_code in (200, 302, 307)
    cfru.assert_called_once()
    kwargs = cfru.call_args.kwargs
    assert kwargs["auth_context"] is fake_ctx
    assert kwargs["received_url"].startswith(
        "https://ut.gitsum.rest/schwab/oauth/callback?"
    )
    assert "state=STATE123" in kwargs["received_url"]
    assert callable(kwargs["token_write_func"])
    bot.client.reload_from_disk.assert_called_once()
    # AuthContext is single-use: it gets popped on success.
    assert "STATE123" not in web._pending_auth_contexts


def test_oauth_callback_rejects_unknown_state(client_with_bot):
    client, bot = client_with_bot
    resp = client.get(
        "/schwab/oauth/callback",
        params={"code": "ABC", "state": "MISSING"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    bot.client.reload_from_disk.assert_not_called()


def test_legacy_oauth_callback_returns_410(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/oauth/callback")
    assert resp.status_code == 410


def test_root_returns_html(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
