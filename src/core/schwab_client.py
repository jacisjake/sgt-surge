"""
SchwabClient — a thin REST wrapper around schwab-py.

Hides account-hash routing, the schwab.orders DSL, and pricehistory enum
mapping from the rest of the bot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

try:
    from schwab.auth import easy_client
except ImportError:  # pragma: no cover — surfaced at install time
    easy_client = None


class SchwabClient:
    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        callback_url: str,
        token_path: str,
        pinned_account_hash: Optional[str] = None,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._callback_url = callback_url
        self._token_path = token_path
        self._pinned_account_hash = pinned_account_hash
        self._client = None
        self._account_hash: Optional[str] = None
        self._load_or_init()

    def _load_or_init(self) -> None:
        if not (self._app_key and self._app_secret):
            logger.warning("[SCHWAB] No app credentials in env — bot starts unauthenticated.")
            return
        try:
            self._client = easy_client(
                api_key=self._app_key,
                app_secret=self._app_secret,
                callback_url=self._callback_url,
                token_path=self._token_path,
            )
            self._resolve_account_hash()
        except (FileNotFoundError, Exception) as e:
            logger.warning(f"[SCHWAB] Could not load token: {e}. Awaiting OAuth via dashboard.")
            self._client = None

    def _resolve_account_hash(self) -> None:
        if self._pinned_account_hash:
            self._account_hash = self._pinned_account_hash
            return
        resp = self._client.get_account_numbers()
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_account_numbers failed: {resp.status_code}")
        accounts = resp.json()
        if not accounts:
            raise RuntimeError("Schwab returned no linked accounts")
        self._account_hash = accounts[0]["hashValue"]
        logger.info(f"[SCHWAB] Using account hash {self._account_hash}")

    def reload_from_disk(self) -> None:
        """Called by the OAuth callback after a fresh token is written."""
        self._load_or_init()

    @property
    def is_authenticated(self) -> bool:
        return self._client is not None and self._account_hash is not None

    @property
    def account_hash(self) -> Optional[str]:
        return self._account_hash
