"""ORB default stops must not attach to swing lots when ORB live is off."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.bot.main import TradingBot


def test_add_default_stops_is_noop_when_orb_live_off():
    bot = MagicMock()
    bot.config = MagicMock(enable_orb_live=False)
    asyncio.run(TradingBot._add_default_stops(bot, "JPM"))
    bot.position_manager.get_position.assert_not_called()


def test_eod_cleanup_skips_externally_opened_positions():
    """EOD flatten is an ORB safety net; swing lots opened outside the bot
    (live_swing places orders straight at the broker) must survive it."""
    from datetime import datetime

    from src.core.position_manager import (
        EXTERNAL_STRATEGY,
        Position,
        PositionSide,
    )

    orb_lot = Position(
        symbol="IOVA",
        side=PositionSide.LONG,
        qty=1.0,
        entry_price=8.0,
        entry_time=datetime.now(),
        strategy="orb",
    )
    swing_lot = Position(
        symbol="UGP",
        side=PositionSide.LONG,
        qty=4.1927,
        entry_price=6.61,
        entry_time=datetime.now(),
        strategy=EXTERNAL_STRATEGY,
    )

    bot = MagicMock()
    bot.executor.cancel_pending_orders = AsyncMock(return_value=0)
    bot.executor.execute_exit = AsyncMock(
        return_value=MagicMock(success=True, position=None)
    )
    bot.position_manager.get_open_positions.return_value = [orb_lot, swing_lot]

    asyncio.run(TradingBot._end_of_day_cleanup(bot))

    exited = [c.kwargs["symbol"] for c in bot.executor.execute_exit.call_args_list]
    assert exited == ["IOVA"], f"EOD closed a swing lot: {exited}"
