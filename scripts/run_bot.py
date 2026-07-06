#!/usr/bin/env python3
"""
Trading bot CLI entry point.

Usage:
    python scripts/run_bot.py           # Run with default config
    python scripts/run_bot.py --help    # Show help
    python scripts/run_bot.py --status  # Show bot status
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.bot.config import get_bot_config
from src.bot.main import TradingBot


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Sgt Schwab - ORB Momentum Day Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/run_bot.py                    # Start the bot
    python scripts/run_bot.py --dry-run          # Show config and exit
    python scripts/run_bot.py --status           # Show account status

Environment variables (or in .env):
    SCHWAB_APP_KEY          - Schwab developer app key
    SCHWAB_APP_SECRET       - Schwab developer app secret
    SCHWAB_OAUTH_REDIRECT_URI - OAuth callback URL
    SCHWAB_TOKEN_PATH       - Path to persisted OAuth token JSON
    SCHWAB_ACCOUNT_HASH     - Pinned account hash (optional)
    TRADING_MODE            - dry_run or live (default: dry_run)
    FMP_API_KEY             - Financial Modeling Prep API key (optional)

See .env.example for all available settings.
        """,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show configuration and exit without running",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current account and position status",
    )

    return parser.parse_args()


def show_config():
    """Display current configuration."""
    config = get_bot_config()

    print("=" * 60)
    print("TRADING BOT CONFIGURATION")
    print("=" * 60)
    print()
    print(f"Trading Mode: {config.trading_mode.value.upper()}")
    print()
    print("Risk Settings:")
    print(f"  Max Position Risk: {config.max_position_risk_pct:.1%}")
    print(f"  Max Portfolio Risk: {config.max_portfolio_risk_pct:.1%}")
    print(f"  Max Drawdown: {config.max_drawdown_pct:.1%}")
    print(f"  Max Positions: {config.max_positions}")
    print(f"  Max Daily Trades: {config.max_daily_trades}")
    print(f"  Max Position % of BP: {config.max_position_pct_of_buying_power:.0%}")
    print()
    print("Scheduler:")
    print(f"  Scanner Interval: {config.stock_check_interval_minutes} min")
    print(f"  Position Monitor: {config.position_monitor_interval_seconds}s")
    print(f"  Broker Sync: {config.broker_sync_interval_minutes} min")
    print(f"  Scanner Refresh: {config.scanner_refresh_interval_minutes} min")
    print()
    print("Scanner:")
    print(f"  Price Range: ${config.scanner_min_price:.2f} - ${config.scanner_max_price:.2f}")
    print(f"  Min Change: {config.scanner_min_change_pct:.0f}%")
    print(f"  Min Dollar Volume: ${config.scanner_min_dollar_volume:,.0f}")
    print(f"  Float Filter: {'ON' if config.scanner_enable_float_filter else 'OFF'}")
    print(f"  Min Float: {config.scanner_min_float_millions}M shares")
    print()
    print("Strategy:")
    print(f"  Timeframe: {config.stock_timeframe}")
    print(f"  ATR Stop Multiplier: {config.stock_atr_stop_multiplier}x")
    print(f"  Risk/Reward Target: {config.risk_reward_target}R")
    print()
    print("=" * 60)


async def show_status():
    """Display current account and position status."""
    from src.core.schwab_client import SchwabClient
    from src.core.position_manager import PositionManager

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )
    position_manager = PositionManager()

    print("=" * 60)
    print("ACCOUNT STATUS")
    print("=" * 60)
    print()

    try:
        account = client.get_account()
        print(f"Equity:       ${float(account['equity']):,.2f}")
        print(f"Buying Power: ${float(account['buying_power']):,.2f}")
        print(f"Cash:         ${float(account['cash']):,.2f}")
        print(f"Status:       {account['status']}")
        print()

        positions = client.get_positions()
        print(f"Open Positions: {len(positions)}")
        print()

        if positions:
            print("Positions:")
            print("-" * 60)
            total_value = 0
            total_pnl = 0

            for p in positions:
                symbol = p["symbol"]
                qty = float(p["qty"])
                entry = float(p["avg_entry_price"])
                current = float(p["current_price"])
                value = float(p["market_value"])
                pnl = float(p["unrealized_pl"])
                pnl_pct = float(p["unrealized_plpc"]) * 100

                total_value += value
                total_pnl += pnl

                arrow = "▲" if pnl >= 0 else "▼"
                print(f"  {symbol:8} {qty:>8.4f} @ ${entry:>8.2f} → ${current:>8.2f}")
                print(f"           Value: ${value:>8.2f}  P&L: {arrow} ${pnl:>8.2f} ({pnl_pct:+.1f}%)")

            print("-" * 60)
            arrow = "▲" if total_pnl >= 0 else "▼"
            print(f"  Total Value: ${total_value:,.2f}  Total P&L: {arrow} ${total_pnl:,.2f}")

    except Exception as e:
        print(f"Error: {e}")

    print()
    print("=" * 60)


async def run_with_api():
    """Run bot with API server."""
    import uvicorn
    from src.bot.web import app, set_bot
    from src.bot.config import get_bot_config
    from src.bot.main import TradingBot, setup_signal_handlers

    config = get_bot_config()
    bot = TradingBot(config)
    setup_signal_handlers(bot)

    # Give API access to bot
    set_bot(bot)

    # Create API server config
    api_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
    )
    api_server = uvicorn.Server(api_config)

    # Run bot and API concurrently
    await asyncio.gather(
        bot.start(),
        api_server.serve(),
    )


def main():
    """Main entry point."""
    args = parse_args()

    if args.dry_run:
        show_config()
        return 0

    if args.status:
        asyncio.run(show_status())
        return 0

    # Run the bot with API
    print("Starting trading bot...")
    print("Dashboard: http://localhost:8080")
    print("Press Ctrl+C to stop")
    print()

    try:
        asyncio.run(run_with_api())
    except KeyboardInterrupt:
        print("\nShutdown requested...")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
