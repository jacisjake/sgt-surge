"""
Order Executor - Handle order placement with retries and verification.

Responsible for:
- Submitting orders with proper error handling
- Retrying on transient failures
- Verifying fills
- Managing order lifecycle
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from loguru import logger

from .schwab_client import SchwabClient


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class OrderResult:
    """Result of an order execution attempt."""

    success: bool
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    filled_price: Optional[float] = None
    error: Optional[str] = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "filled_price": self.filled_price,
            "error": self.error,
            "dry_run": self.dry_run,
        }


class OrderExecutor:
    """
    Handles order execution with retries and verification.

    Features:
    - Automatic retries on transient failures
    - Order fill verification
    - Proper error classification
    - Logging of all order activity
    """

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 1.0
    FILL_CHECK_INTERVAL = 0.5
    FILL_TIMEOUT_SECONDS = 30.0

    # Error messages that indicate transient failures (retry-able)
    TRANSIENT_ERRORS = [
        "connection",
        "timeout",
        "rate limit",
        "temporarily",
        "try again",
        "503",
        "504",
    ]

    def __init__(
        self,
        client: SchwabClient,
        trading_mode=None,
        allow_fractional: bool = True,
    ):
        from config.settings import TradingMode

        self.client = client
        if trading_mode is None:
            trading_mode = TradingMode.LIVE
        self.trading_mode = trading_mode
        # Fractional quantities are the norm on a small account: at
        # risk_pct*equity/stop_frac (~$24/position on $200) almost every order
        # is a fraction of a share. Schwab accepts them — the live lab path has
        # filled fractional buys. It is a constructor argument rather than an
        # attribute poked after construction so a caller cannot silently
        # inherit the wrong mode by forgetting to set it; that mismatch left
        # the bot rejecting every stop order for a fractional position locally.
        self.allow_fractional = allow_fractional

    def execute_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        wait_for_fill: bool = True,
    ) -> OrderResult:
        """
        Execute a market order with retries.

        Args:
            symbol: Stock symbol
            qty: Quantity to trade
            side: "buy" or "sell"
            wait_for_fill: Whether to wait for order to fill

        Returns:
            OrderResult with execution details
        """
        return self._execute_with_retry(
            order_type="market",
            symbol=symbol,
            qty=qty,
            side=side,
            wait_for_fill=wait_for_fill,
        )

    def execute_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        wait_for_fill: bool = False,
        extended_hours: bool = False,
    ) -> OrderResult:
        """
        Execute a limit order with retries.

        Args:
            symbol: Stock symbol
            qty: Quantity to trade
            side: "buy" or "sell"
            limit_price: Limit price
            wait_for_fill: Whether to wait for order to fill

        Returns:
            OrderResult with execution details
        """
        return self._execute_with_retry(
            order_type="limit",
            symbol=symbol,
            qty=qty,
            side=side,
            limit_price=limit_price,
            wait_for_fill=wait_for_fill,
            extended_hours=extended_hours,
        )

    def execute_stop_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
        limit_price: float,
    ) -> OrderResult:
        """
        Execute a stop-limit order (for stop-losses).

        These are submitted but not expected to fill immediately.
        """
        return self._execute_with_retry(
            order_type="stop_limit",
            symbol=symbol,
            qty=qty,
            side=side,
            stop_price=stop_price,
            limit_price=limit_price,
            wait_for_fill=False,  # Stop orders wait for trigger
        )

    def _execute_with_retry(
        self,
        order_type: str,
        symbol: str,
        qty: float,
        side: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        wait_for_fill: bool = True,
        extended_hours: bool = False,
    ) -> OrderResult:
        """
        Execute order with retry logic.

        Retries on transient failures, fails fast on permanent errors.
        """
        from config.settings import TradingMode

        # Dry-run intercept: fabricate a fill at the current quote price
        if self.trading_mode == TradingMode.DRY_RUN:
            return self._dry_run_fill(symbol=symbol, qty=qty, side=side)

        last_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                # Submit order based on type
                order_id = self._submit_order(
                    order_type=order_type,
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    extended_hours=extended_hours,
                )

                if order_id is None:
                    raise ValueError("Order submission returned None")

                logger.debug(f"Order submitted: {order_id} (attempt {attempt})")

                # Wait for fill if requested
                if wait_for_fill:
                    return self._wait_for_fill(order_id)
                else:
                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        status=OrderStatus.SUBMITTED,
                    )

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Order attempt {attempt}/{self.MAX_RETRIES} failed: {last_error}"
                )

                # Check if error is transient (retry-able)
                if self._is_transient_error(last_error):
                    if attempt < self.MAX_RETRIES:
                        time.sleep(self.RETRY_DELAY_SECONDS * attempt)
                        continue
                else:
                    # Permanent error, don't retry
                    break

        # All retries exhausted
        logger.error(f"Order failed after {self.MAX_RETRIES} attempts: {last_error}")
        return OrderResult(
            success=False,
            status=OrderStatus.FAILED,
            error=last_error,
        )

    def _dry_run_fill(self, symbol: str, qty: float, side: str) -> OrderResult:
        """Fabricate a fill at the current quote price without sending any order."""
        from datetime import datetime

        price = self.client.get_latest_price(symbol)
        order_id = f"DRYRUN-{symbol}-{datetime.utcnow().isoformat()}"
        logger.info(
            f"[DRY RUN] {side.upper()} {qty} {symbol} @ {price:.4f} (fabricated fill)"
        )
        return OrderResult(
            success=True,
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_qty=qty,
            filled_price=price,
            error=None,
            dry_run=True,
        )

    def _submit_order(
        self,
        order_type: str,
        symbol: str,
        qty: float,
        side: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        extended_hours: bool = False,
    ) -> str:
        """Submit order to Schwab based on type. Returns order_id string.

        Quantity handling depends on `allow_fractional` (default True):
          - True: round to 4 decimals, reject only <= 0. Required on a small
            account, where position sizing lands well below one share of most
            names.
          - False: round DOWN to whole shares, reject < 1. For callers that
            must trade whole lots.
        """
        original_qty = qty
        if self.allow_fractional:
            qty = round(float(qty), 4)
            if qty <= 0:
                raise ValueError(f"Cannot buy 0 shares of {symbol}")
        else:
            qty = int(qty)
            if qty < 1:
                raise ValueError(f"Cannot buy less than 1 share of {symbol}")
        if qty != original_qty:
            logger.info(f"Adjusted {symbol} qty {original_qty:.6f} -> {qty}")

        if order_type == "market":
            return self.client.submit_market_order(symbol, qty, side)
        elif order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit orders")
            return self.client.submit_limit_order(symbol, qty, side, limit_price)
        elif order_type == "stop_limit":
            if stop_price is None or limit_price is None:
                raise ValueError("stop_price and limit_price required for stop-limit")
            return self.client.submit_stop_limit_order(
                symbol, qty, side, stop_price, limit_price
            )
        else:
            raise ValueError(f"Unknown order type: {order_type}")

    def _wait_for_fill(self, order_id: str) -> OrderResult:
        """Wait for order to fill or timeout.

        Schwab order statuses (lower-cased): filled, working,
        pending_activation, queued, awaiting_parent_order, canceled,
        rejected, expired, replaced. The executor must keep polling on
        any non-terminal status -- including the "not found yet" window
        right after submit. Treating None or unknown statuses as terminal
        causes the bot to abandon orders that Schwab actually fills, as
        observed on the IOVA trade 2026-06-04.
        """
        start_time = time.time()
        consecutive_not_found = 0

        while time.time() - start_time < self.FILL_TIMEOUT_SECONDS:
            order = self.client.get_order(order_id)

            if order is None:
                # Eventual-consistency window or stale ID. Keep polling
                # until the order resolves or the timeout fires. Only
                # treat as truly missing after enough consecutive misses
                # that we believe Schwab really doesn't have it.
                consecutive_not_found += 1
                if consecutive_not_found >= 20:  # ~10s of misses
                    return OrderResult(
                        success=False,
                        order_id=order_id,
                        status=OrderStatus.FAILED,
                        error="Order not found after 10s of polling",
                    )
                time.sleep(self.FILL_CHECK_INTERVAL)
                continue
            consecutive_not_found = 0

            status = order["status"]

            if status == "filled":
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    status=OrderStatus.FILLED,
                    filled_qty=order["filled_qty"],
                    filled_price=order.get("price"),
                )
            # Non-terminal statuses to keep polling on
            elif status in {"partially_filled", "working", "pending_activation",
                            "queued", "awaiting_parent_order", "accepted",
                            "pending_replace", "pending_cancel"}:
                pass
            # Terminal failure statuses (accept both spellings of "canceled")
            elif status in {"cancelled", "canceled", "expired", "rejected", "replaced"}:
                # Normalize "canceled" -> "cancelled" for the enum.
                mapped = "cancelled" if status in {"cancelled", "canceled"} else status
                return OrderResult(
                    success=False,
                    order_id=order_id,
                    status=OrderStatus(mapped),
                    filled_qty=order["filled_qty"],
                    error=f"Order {status}",
                )
            else:
                # Unknown status -- keep polling and log so we can
                # extend the recognized set if Schwab adds new states.
                logger.warning(
                    f"[EXECUTOR] {order_id}: unknown status {status!r}; "
                    f"continuing to poll"
                )

            time.sleep(self.FILL_CHECK_INTERVAL)

        # Timeout - check final status
        order = self.client.get_order(order_id)

        if order and order["filled_qty"] > 0:
            # Partial fill
            return OrderResult(
                success=True,  # Partial success
                order_id=order_id,
                status=OrderStatus.PARTIALLY_FILLED,
                filled_qty=order["filled_qty"],
                filled_price=order.get("price"),
            )

        # Cancel the order
        self.client.cancel_order(order_id)

        return OrderResult(
            success=False,
            order_id=order_id,
            status=OrderStatus.EXPIRED,
            error="Order fill timeout",
        )

    def _is_transient_error(self, error_message: str) -> bool:
        """Check if error is transient (should retry)."""
        error_lower = error_message.lower()
        return any(term in error_lower for term in self.TRANSIENT_ERRORS)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        return self.client.cancel_order(order_id)

    def cancel_all_orders(self) -> int:
        """Cancel all open orders."""
        return self.client.cancel_all_orders()

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        return self.client.get_orders(status="open")

    def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """Get current status of an order."""
        order = self.client.get_order(order_id)

        if order is None:
            return None

        status_map = {
            "new": OrderStatus.SUBMITTED,
            "accepted": OrderStatus.SUBMITTED,
            "pending_new": OrderStatus.PENDING,
            "filled": OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED,
        }

        return OrderResult(
            success=order["status"] == "filled",
            order_id=order_id,
            status=status_map.get(order["status"], OrderStatus.PENDING),
            filled_qty=order["filled_qty"],
            filled_price=order.get("price"),
        )
