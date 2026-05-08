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

    def get_account(self) -> dict:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_account(self._account_hash, fields=["positions"])
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_account failed: {resp.status_code}")
        sa = resp.json()["securitiesAccount"]
        bal = sa.get("currentBalances", {})
        return {
            "equity": float(bal.get("liquidationValue", 0)),
            "buying_power": float(bal.get("buyingPower", 0)),
            "cash": float(bal.get("cashAvailableForTrading", 0)),
            "daytrade_count": int(sa.get("roundTrips", 0)),
            "is_pdt": bool(sa.get("isDayTrader", False)),
            "type": sa.get("type", ""),
            "status": "active",
            "_raw_positions": sa.get("positions", []),
        }

    def get_buying_power(self) -> float:
        return self.get_account()["buying_power"]

    def get_equity(self) -> float:
        return self.get_account()["equity"]

    def get_positions(self) -> list[dict]:
        positions = self.get_account()["_raw_positions"]
        out = []
        for p in positions:
            qty = float(p.get("longQuantity", 0)) - float(p.get("shortQuantity", 0))
            if qty == 0:
                continue
            mkt = float(p.get("marketValue", 0))
            current_price = mkt / qty if qty else 0.0
            out.append({
                "symbol": p["instrument"]["symbol"],
                "qty": qty,
                "avg_entry_price": float(p.get("averagePrice", 0)),
                "current_price": current_price,
                "market_value": mkt,
                "unrealized_pl": float(p.get("currentDayProfitLoss", 0)),
                "unrealized_plpc": float(p.get("currentDayProfitLossPercentage", 0)) / 100,
            })
        return out

    def get_position(self, symbol: str) -> Optional[dict]:
        for p in self.get_positions():
            if p["symbol"] == symbol:
                return p
        return None

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None
