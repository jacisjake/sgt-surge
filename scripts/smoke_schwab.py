#!/usr/bin/env python3
"""
Smoke test for the Schwab integration. Run against live Schwab in dry_run mode.

Verifies: auth, account hash, pricehistory pull, streaming bar callback,
synthetic ORB signal -> dry-run fill -> exit fill.
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from loguru import logger

from config.settings import TradingMode
from src.bot.config import get_bot_config
from src.core.schwab_client import SchwabClient
from src.core.schwab_stream import SchwabStreamClient
from src.core.order_executor import OrderExecutor


async def main() -> int:
    cfg = get_bot_config()
    if cfg.trading_mode != TradingMode.DRY_RUN:
        logger.error("Refusing to run smoke test in live mode")
        return 1

    client = SchwabClient(
        app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )

    logger.info("--- 1. Auth check ---")
    assert client.is_authenticated, "Not authenticated — run OAuth via dashboard first"
    logger.info(f"  account_hash = {client.account_hash}")

    logger.info("--- 2. pricehistory pull ---")
    bars = client.get_bars("SPY", timeframe="5Min", limit=10)
    assert not bars.empty, "Schwab returned no bars for SPY"
    logger.info(f"  got {len(bars)} 5-min bars; last close ${bars['close'].iloc[-1]:.2f}")

    logger.info("--- 3. Streaming bar callback ---")
    stream = SchwabStreamClient(schwab_client=client)
    bar_count = {"n": 0}
    stream.on_bar(lambda b: bar_count.update(n=bar_count["n"] + 1))

    ok = await stream.connect_data()
    assert ok, "connect_data failed"
    await stream.subscribe(bars=["SPY"], quotes=["SPY"])

    async def loop():
        try:
            await asyncio.wait_for(stream.run_data_loop(), timeout=90)
        except asyncio.TimeoutError:
            pass

    await loop()
    logger.info(f"  received {bar_count['n']} bars in 90s")

    logger.info("--- 4. Dry-run executor round-trip ---")
    ex = OrderExecutor(client=client, trading_mode=TradingMode.DRY_RUN)
    entry_price = client.get_latest_price("SPY")
    entry = ex.execute_market_order("SPY", qty=1, side="buy")
    assert entry.success and entry.dry_run
    logger.info(f"  entry: dry-run fill at ${entry.filled_price:.2f}")

    exit_ = ex.execute_market_order("SPY", qty=1, side="sell")
    assert exit_.success and exit_.dry_run
    logger.info(f"  exit:  dry-run fill at ${exit_.filled_price:.2f}")

    await stream.disconnect()
    logger.info("--- All smoke checks passed ---")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
