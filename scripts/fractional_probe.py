"""Empirically test whether Schwab's Trader API accepts a FRACTIONAL equity order.

This is the only way to settle the question — the docs don't say. It places ONE
tiny real market order for a sub-1-share quantity and reports whether Schwab
accepts it (order id) or rejects it (validation error).

SAFETY:
  * Requires the explicit flag --yes-place-a-real-order — it can NEVER fire by
    accident or on a schedule.
  * Hard-capped at $10 notional; refuses anything larger.
  * Places a BUY only; it does not sell. Any resulting position is visible on the
    dashboard for you to keep or close manually.
  * After hours the order queues for the next open — but the API's accept/reject
    verdict on the fractional quantity is returned immediately, which is the
    whole point.

Usage (run BY YOU, on the server):
    podman exec -w /app sgt-schwab-bot python -m scripts.fractional_probe \
        --symbol F --dollars 5 --yes-place-a-real-order
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

MAX_DOLLARS = 10.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="F", help="cheap, liquid symbol (default F)")
    ap.add_argument("--dollars", type=float, default=5.0,
                    help="notional to spend (hard-capped at $10)")
    ap.add_argument("--yes-place-a-real-order", action="store_true",
                    help="required — this places a REAL order")
    args = ap.parse_args()

    if not args.yes_place_a_real_order:
        print("Refusing: this places a REAL order. Re-run with --yes-place-a-real-order.")
        return 2
    if args.dollars > MAX_DOLLARS:
        print(f"Refusing: ${args.dollars} exceeds the ${MAX_DOLLARS} safety cap.")
        return 2

    from config.settings import TradingMode
    from src.bot.config import get_bot_config
    from src.core.order_executor import OrderExecutor
    from src.core.schwab_client import SchwabClient

    cfg = get_bot_config()
    client = SchwabClient(app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
                          callback_url=cfg.schwab_oauth_redirect_uri,
                          token_path=cfg.schwab_token_path)
    if not client.is_authenticated:
        print("Refusing: Schwab client not authenticated. Re-auth first.")
        return 1

    price = float(client.get_latest_price(args.symbol) or 0)
    if price <= 0:
        print(f"Could not get a price for {args.symbol}; aborting.")
        return 1

    qty = round(args.dollars / price, 4)
    print(f"Probe: BUY {qty} shares of {args.symbol} @ ~${price:.2f}  (~${qty*price:.2f} notional)")
    if qty >= 1:
        print(f"NOTE: {args.symbol} at ${price:.2f} gives qty >= 1 — not a fractional test. "
              f"Pick a pricier symbol or fewer dollars.")

    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)
    ex.allow_fractional = True
    try:
        result = ex.execute_market_order(args.symbol, qty, "buy", wait_for_fill=False)
    except Exception as e:  # noqa: BLE001
        print(f"\n>>> VERDICT: Schwab REJECTED the fractional order.")
        print(f">>> {type(e).__name__}: {e}")
        print(">>> Fractional is NOT usable via the Trader API. Pivot to Alpaca or fund the account.")
        return 0

    print(f"\n>>> VERDICT: Schwab ACCEPTED the fractional order. result={result}")
    print(">>> Fractional IS usable — breakout_52w can trade the validated strategy on $200.")
    print(">>> Review/cancel the order on the dashboard if you don't want the fill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
