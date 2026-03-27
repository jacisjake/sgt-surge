"""
Momentum day trading scheduler.

Manages scheduled jobs for the trading day:
- Pre-market scanning (6:00 AM ET)
- Active momentum scanning + signal generation (6:00 AM - 4:00 PM ET)
- Safety net close-all (3:55 PM ET)
- Daily reset (6:00 AM ET)

Uses APScheduler with Eastern Time for all trading jobs.
Uses schedule-based NYSE holiday list for market status.
"""

from datetime import datetime, time
from typing import Callable, Optional

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.bot.config import BotConfig
from src.core.tastytrade_client import NYSE_HOLIDAYS

ET = pytz.timezone("America/New_York")


class BotScheduler:
    """
    Day trading scheduler.

    Schedule (all times Eastern):
    - 06:00 AM: Daily reset + start scanning
    - 06:00 AM - 4:00 PM: Active scanning + signals (every 5 min)
    - 3:55 PM: Safety net close-all
    """

    def __init__(self, config: BotConfig):
        """
        Initialize scheduler.

        Args:
            config: Bot configuration
        """
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone="America/New_York")

        # Job callbacks (set by TradingBot)
        self._momentum_scan_callback: Optional[Callable] = None
        self._press_release_scan_callback: Optional[Callable] = None
        self._position_monitor_callback: Optional[Callable] = None
        self._broker_sync_callback: Optional[Callable] = None
        self._end_of_day_callback: Optional[Callable] = None
        self._daily_reset_callback: Optional[Callable] = None

        # Track state
        self._is_running = False

        self._premarket_start = self._parse_time(config.premarket_scan_start)

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse HH:MM string to time object."""
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))

    def set_callbacks(
        self,
        momentum_scan: Optional[Callable] = None,
        press_release_scan: Optional[Callable] = None,
        end_of_day: Optional[Callable] = None,
        daily_reset: Optional[Callable] = None,
    ) -> None:
        """
        Set job callbacks.

        Note: position_monitor and broker_sync removed — replaced by WebSocket streaming.

        Args:
            momentum_scan: Callback for momentum scanner + signal generation
            press_release_scan: Callback for pre-market press release RSS scanning
            end_of_day: Callback for end-of-day cleanup (close positions, cancel orders)
            daily_reset: Callback for daily reset (clear counters, refresh state)
        """
        self._momentum_scan_callback = momentum_scan
        self._press_release_scan_callback = press_release_scan
        self._end_of_day_callback = end_of_day
        self._daily_reset_callback = daily_reset

    def setup_jobs(self) -> None:
        """Configure all scheduled jobs for momentum day trading."""

        pre_h = self._premarket_start.hour
        pre_m = self._premarket_start.minute

        # ── 0. Press release scan: 4 AM + 9:15 AM ET ─────────────────────
        # Two scans: overnight PRs at 4 AM, last-minute earnings at 9:15 AM
        if self._press_release_scan_callback:
            pr_start = self._parse_time(self.config.press_release_scan_start)

            self.scheduler.add_job(
                self._run_press_release_scan,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=str(pr_start.hour),
                    minute=str(pr_start.minute),
                    timezone="America/New_York",
                ),
                id="press_release_scan_early",
                name="Press Release Scan (4 AM)",
                replace_existing=True,
            )

            self.scheduler.add_job(
                self._run_press_release_scan,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="9",
                    minute="15",
                    timezone="America/New_York",
                ),
                id="press_release_scan_preopen",
                name="Press Release Scan (9:15 AM)",
                replace_existing=True,
            )

        # ── 1. Momentum scan: handled by asyncio loop in main.py ────────
        # (APScheduler cron was unreliable — timer chain broke under DXLink load)

        # NOTE: Position monitor and broker sync removed — replaced by WebSocket streaming
        # Position exits now handled by real-time quote callbacks (StreamHandler.on_quote)
        # Broker state now handled by trade update stream (StreamHandler.on_trade_update)

        # ── 3. Safety net and EOD cleanup ────────────────────────────────
        if self._end_of_day_callback:

            # ── 6. Safety net close-all: 3:55 PM ET ─────────────────────
            self.scheduler.add_job(
                self._run_end_of_day,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="15",
                    minute="55",
                    timezone="America/New_York",
                ),
                id="safety_net_close",
                name="Safety Net Close-All (3:55 PM)",
                replace_existing=True,
            )

        # ── 7. Daily reset: 6:00 AM ET ──────────────────────────────────
        if self._daily_reset_callback:
            self.scheduler.add_job(
                self._run_daily_reset,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=str(pre_h),
                    minute=str(pre_m),
                    timezone="America/New_York",
                ),
                id="daily_reset",
                name="Daily Reset",
                replace_existing=True,
            )

    # ── Market Clock (Schedule-Based) ────────────────────────────────────

    def is_trading_day(self) -> bool:
        """
        Check if today is a trading day (not weekend, not holiday).

        Uses static NYSE holiday list for accurate holiday detection.
        """
        now_et = datetime.now(ET)

        # Weekend check (fast path)
        if now_et.weekday() >= 5:
            return False

        # Holiday check
        if now_et.date() in NYSE_HOLIDAYS:
            return False

        return True

    # ── Time Helpers ──────────────────────────────────────────────────────

    def is_in_premarket(self) -> bool:
        """Check if we're before market open (9:30 AM ET) on a trading day."""
        if not self.is_trading_day():
            return False

        now_et = datetime.now(ET)
        current_time = now_et.time()
        return self._premarket_start <= current_time < time(9, 30)

    def is_market_open(self) -> bool:
        """
        Check if US stock market is currently open.

        Uses schedule-based check with NYSE holiday list.
        """
        now_et = datetime.now(ET)

        if now_et.weekday() >= 5:
            return False

        if now_et.date() in NYSE_HOLIDAYS:
            return False

        current_time = now_et.time()
        market_open = time(9, 30)
        market_close = time(16, 0)
        return market_open <= current_time < market_close

    # ── Job Runners (with error handling) ────────────────────────────────

    async def _run_press_release_scan(self) -> None:
        """Run press release catalyst scanner with error handling."""
        if self._press_release_scan_callback:
            try:
                await self._press_release_scan_callback()
            except Exception as e:
                logger.error(f"Press release scan error: {e}")

    async def _run_momentum_scan(self) -> None:
        """Run momentum scanner with error handling."""
        if self._momentum_scan_callback:
            try:
                await self._momentum_scan_callback()
            except Exception as e:
                logger.error(f"Momentum scan error: {e}")

    async def _run_position_monitor(self) -> None:
        """Run position monitor with error handling."""
        if self._position_monitor_callback:
            try:
                await self._position_monitor_callback()
            except Exception as e:
                logger.error(f"Position monitor error: {e}")

    async def _run_broker_sync(self) -> None:
        """Run broker sync with error handling."""
        if self._broker_sync_callback:
            try:
                await self._broker_sync_callback()
            except Exception as e:
                logger.error(f"Broker sync error: {e}")

    async def _run_end_of_day(self) -> None:
        """Run end-of-day cleanup with error handling."""
        if self._end_of_day_callback:
            try:
                logger.info("Running end-of-day cleanup...")
                await self._end_of_day_callback()
            except Exception as e:
                logger.error(f"End-of-day cleanup error: {e}")

    async def _run_daily_reset(self) -> None:
        """Run daily reset with error handling."""
        if self._daily_reset_callback:
            try:
                logger.info("Running daily reset...")
                await self._daily_reset_callback()
            except Exception as e:
                logger.error(f"Daily reset error: {e}")

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler."""
        if not self._is_running:
            self.setup_jobs()
            self.scheduler.start()
            self._is_running = True
            logger.info(
                f"Scheduler started | "
                f"Scanning: 6:00 AM - 4:00 PM ET (every 5 min) | "
                f"Monitor: every {self.config.position_monitor_interval_seconds}s"
            )

    def stop(self) -> None:
        """Stop the scheduler."""
        if self._is_running:
            self.scheduler.shutdown(wait=True)
            self._is_running = False
            logger.info("Scheduler stopped")

    def pause(self) -> None:
        """Pause all jobs."""
        self.scheduler.pause()
        logger.info("Scheduler paused")

    def resume(self) -> None:
        """Resume all jobs."""
        self.scheduler.resume()
        logger.info("Scheduler resumed")

    def get_jobs(self) -> list[dict]:
        """Get list of scheduled jobs with next run times."""
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self.scheduler.get_jobs()
        ]

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running
