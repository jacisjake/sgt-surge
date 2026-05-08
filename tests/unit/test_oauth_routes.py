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
    yield TestClient(web.app), bot


def test_oauth_start_redirects_to_schwab_authorize(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/schwab/oauth/start", follow_redirects=False)
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://api.schwabapi.com/v1/oauth/authorize")
    assert "client_id=K" in location
    assert "redirect_uri=https" in location


def test_oauth_callback_persists_token_and_reloads_client(client_with_bot):
    client, bot = client_with_bot
    with patch("src.bot.web.client_from_received_url") as cfru:
        cfru.return_value = MagicMock()
        resp = client.get(
            "/schwab/oauth/callback",
            params={"code": "ABC", "session": "S"},
            follow_redirects=False,
        )
    assert resp.status_code in (200, 302, 307)
    cfru.assert_called_once()
    bot.client.reload_from_disk.assert_called_once()


def test_legacy_oauth_callback_returns_410(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/oauth/callback")
    assert resp.status_code == 410


def test_root_returns_html(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
