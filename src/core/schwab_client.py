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
import pandas as pd
from loguru import logger

try:
    from schwab.auth import client_from_token_file
    from schwab.client import Client as _SchwabSyncClient
    from schwab.orders.equities import (
        equity_buy_market,
        equity_sell_market,
        equity_buy_limit,
        equity_sell_limit,
    )
    from schwab.orders.generic import OrderBuilder as _OrderBuilder
    from schwab.orders.common import (
        Duration as _Duration,
        Session as _Session,
        OrderType as _OrderType,
        EquityInstruction as _EquityInstruction,
    )
except ImportError:  # pragma: no cover — surfaced at install time
    client_from_token_file = None
    _SchwabSyncClient = None
    equity_buy_market = equity_sell_market = equity_buy_limit = equity_sell_limit = None
    _OrderBuilder = _Duration = _Session = _OrderType = _EquityInstruction = None


def _avg_fill_price(order: dict) -> Optional[float]:
    """Volume-weighted average fill price across an order's execution legs.

    Schwab reports per-fill prices in orderActivityCollection[].executionLegs[]
    rather than a single top-level price for market orders. Returns None when
    nothing has executed yet.
    """
    total_qty = 0.0
    total_notional = 0.0
    for activity in order.get("orderActivityCollection") or []:
        for leg in activity.get("executionLegs") or []:
            qty = float(leg.get("quantity", 0) or 0)
            price = leg.get("price")
            if qty <= 0 or price is None:
                continue
            total_qty += qty
            total_notional += qty * float(price)
    if total_qty <= 0:
        return None
    return total_notional / total_qty


class SchwabClient:
    _TIMEFRAME_TO_METHOD = {
        "1Min": "get_price_history_every_minute",
        "5Min": "get_price_history_every_five_minutes",
        "15Min": "get_price_history_every_fifteen_minutes",
        "30Min": "get_price_history_every_thirty_minutes",
        "1Hour": "get_price_history_every_thirty_minutes",
        "1Day": "get_price_history_every_day",
    }

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
        # Use client_from_token_file directly rather than easy_client because
        # our callback URL is ut.gitsum.rest (not 127.0.0.1) and easy_client
        # tries to fall back to client_from_login_flow when the token nears
        # its max_token_age (6.5d default), which fails with "Disallowed
        # hostname" on our deployment.
        try:
            self._client = client_from_token_file(
                self._token_path, self._app_key, self._app_secret
            )
            self._resolve_account_hash()
        except FileNotFoundError as e:
            logger.warning(
                f"[SCHWAB] Token file missing at {self._token_path}: {e}. "
                f"Awaiting OAuth via dashboard."
            )
            self._client = None
        except Exception as e:
            logger.warning(
                f"[SCHWAB] Could not load token: {e}. Awaiting OAuth via dashboard."
            )
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
        resp = self._client.get_account(
            self._account_hash,
            fields=[_SchwabSyncClient.Account.Fields.POSITIONS],
        )
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_account failed: {resp.status_code}")
        sa = resp.json()["securitiesAccount"]
        bal = sa.get("currentBalances", {})
        # Cash accounts have no `buyingPower` field; Schwab reports the
        # tradable balance as cashAvailableForTrading. Fall through so the
        # field works for both account types.
        buying_power = float(
            bal.get("buyingPower")
            or bal.get("cashAvailableForTrading")
            or 0
        )
        return {
            "equity": float(bal.get("liquidationValue", 0)),
            "buying_power": buying_power,
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

    def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 100) -> "pd.DataFrame":
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        method_name = self._TIMEFRAME_TO_METHOD.get(timeframe)
        if not method_name:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        method = getattr(self._client, method_name)
        resp = method(symbol)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"pricehistory failed: {resp.status_code}")
        candles = resp.json().get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        return df.tail(limit)

    def get_latest_price(self, symbol: str) -> float:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_quote(symbol)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_quote failed: {resp.status_code}")
        return float(resp.json()[symbol]["quote"]["lastPrice"])

    def get_latest_quotes_with_change(self, symbols: list[str]) -> dict:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_quotes(symbols)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_quotes failed: {resp.status_code}")
        out = {}
        for sym, payload in resp.json().items():
            q = payload.get("quote", {})
            out[sym] = {
                "price": float(q.get("lastPrice", 0)),
                "bid": float(q.get("bidPrice", 0)),
                "ask": float(q.get("askPrice", 0)),
                "change": float(q.get("netChange", 0)),
                "change_pct": float(q.get("netPercentChangeInDouble", 0)),
            }
        return out

    @staticmethod
    def _extract_order_id_from_location(headers: dict) -> str:
        location = headers.get("Location") or headers.get("location") or ""
        return location.rsplit("/", 1)[-1]

    def submit_market_order(self, symbol: str, qty: float, side: str) -> str:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        builder = (
            equity_buy_market(symbol, int(qty))
            if side.lower() == "buy"
            else equity_sell_market(symbol, int(qty))
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def submit_limit_order(
        self, symbol: str, qty: float, side: str, limit_price: float
    ) -> str:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        builder = (
            equity_buy_limit(symbol, int(qty), limit_price)
            if side.lower() == "buy"
            else equity_sell_limit(symbol, int(qty), limit_price)
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def submit_stop_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
        limit_price: float,
    ) -> str:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")

        instr = (
            _EquityInstruction.BUY if side.lower() == "buy"
            else _EquityInstruction.SELL
        )
        builder = (
            _OrderBuilder()
            .set_order_type(_OrderType.STOP_LIMIT)
            .set_session(_Session.NORMAL)
            .set_duration(_Duration.DAY)
            .set_stop_price(stop_price)
            .set_price(limit_price)
            .add_equity_leg(
                instruction=instr,
                symbol=symbol,
                quantity=int(qty),
            )
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.cancel_order(order_id, self._account_hash)
        return resp.status_code in (200, 201)

    def cancel_all_orders(self) -> int:
        cancelled = 0
        for order in self.get_orders(status="open"):
            if self.cancel_order(order["id"]):
                cancelled += 1
        return cancelled

    _OPEN_STATUSES = {"WORKING", "PENDING_ACTIVATION", "QUEUED", "AWAITING_PARENT_ORDER"}

    def get_orders(self, status: str = "open") -> list[dict]:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_orders_for_account(self._account_hash)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_orders_for_account failed: {resp.status_code}")
        out = []
        for o in resp.json():
            if status == "open" and o.get("status") not in self._OPEN_STATUSES:
                continue
            leg = (o.get("orderLegCollection") or [{}])[0]
            out.append({
                "id": str(o.get("orderId")),
                "symbol": leg.get("instrument", {}).get("symbol", ""),
                "qty": float(leg.get("quantity", 0)),
                "filled_qty": float(o.get("filledQuantity", 0)),
                "type": str(o.get("orderType", "")).lower(),
                "status": str(o.get("status", "")).lower(),
                "price": float(o["price"]) if o.get("price") is not None else None,
                "stop_price": float(o["stopPrice"]) if o.get("stopPrice") is not None else None,
                "submitted_at": o.get("enteredTime"),
            })
        return out

    def get_order(self, order_id: str) -> Optional[dict]:
        """Direct single-order lookup via schwab-py's get_order(order_id,
        account_hash). Avoids the eventual-consistency race in
        get_orders_for_account, which can omit just-submitted orders for
        ~1s after place_order returns and used to make the executor's
        _wait_for_fill bail with 'Order not found' before the order ever
        appeared in the list."""
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        try:
            resp = self._client.get_order(order_id, self._account_hash)
        except Exception as e:
            logger.warning(f"[SCHWAB] get_order({order_id}) raised: {e}")
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_order failed: {resp.status_code}")
        o = resp.json()
        leg = (o.get("orderLegCollection") or [{}])[0]
        # Market orders carry no top-level "price"; the average fill lives in
        # the execution legs. Fall back to the volume-weighted average so
        # downstream exit/P&L logic never closes a position at price=None.
        price = float(o["price"]) if o.get("price") is not None else _avg_fill_price(o)
        return {
            "id": str(o.get("orderId")),
            "symbol": leg.get("instrument", {}).get("symbol", ""),
            "qty": float(leg.get("quantity", 0)),
            "filled_qty": float(o.get("filledQuantity", 0)),
            "type": str(o.get("orderType", "")).lower(),
            "status": str(o.get("status", "")).lower(),
            "price": price,
            "stop_price": float(o["stopPrice"]) if o.get("stopPrice") is not None else None,
            "submitted_at": o.get("enteredTime"),
        }
