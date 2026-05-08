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
