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


# ── ORB lifecycle must not be scheduled when it cannot trade ───────────────

def _scheduler_jobs(enable_orb_live: bool):
    """Register callbacks the way TradingBot does, and report scheduled jobs."""
    from unittest.mock import MagicMock

    from src.bot.scheduler import BotScheduler

    cfg = MagicMock(enable_orb_live=enable_orb_live)
    sched = BotScheduler(cfg)
    # Callbacks are always registered, exactly as TradingBot does — the
    # scheduler itself must refuse to schedule the ORB lifecycle.
    sched.set_callbacks(
        momentum_scan=lambda: None,
        end_of_day=lambda: None,
        daily_reset=lambda: None,
        or_lock=lambda: None,
    )
    sched.setup_jobs()
    return {j.id for j in sched.scheduler.get_jobs()}


def test_safety_net_close_all_is_not_scheduled_when_orb_live_is_off():
    """A strategy that cannot open a position must not close one.

    The 15:55 flatten closed 23 of 34 positions in August while ORB live was
    already false. Skipping the right positions is one bug away from failing;
    not scheduling the job cannot fail.
    """
    assert "safety_net_close" not in _scheduler_jobs(enable_orb_live=False)


def test_or_lock_is_not_scheduled_when_orb_live_is_off():
    assert "or_lock" not in _scheduler_jobs(enable_orb_live=False)


def test_daily_reset_still_runs_when_orb_live_is_off():
    """Housekeeping is harmless and keeps the dashboard honest."""
    assert "daily_reset" in _scheduler_jobs(enable_orb_live=False)


def test_orb_lifecycle_is_scheduled_when_orb_live_is_on():
    jobs = _scheduler_jobs(enable_orb_live=True)
    assert "safety_net_close" in jobs
    assert "or_lock" in jobs
