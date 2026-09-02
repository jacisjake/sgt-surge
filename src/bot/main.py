"""
Momentum day trading bot.

Orchestrates all components: scanner, signals, processing, execution, monitoring.
Targets low-float stocks ($1-$10, prefer $2+) with pullback entries on 5-min bars.

Architecture:
- WebSocket streaming for real-time bars, quotes, trade updates, news
- APScheduler for time-based events (scanner refresh, EOD cleanup, daily reset)
- REST API for scanner (no WSS equivalent), account info, order submission
"""

import asyncio
from pathlib import Path
import logging
import signal
import sys
from datetime import datetime, time as dtime, timezone
from typing import Optional

from loguru import logger

from src.bot.config import BotConfig, get_bot_config
from src.bot.executor import TradeExecutor
from src.bot.float_provider import FloatDataProvider
from src.bot.monitor import PositionMonitor
from src.bot.processor import SignalProcessor
from src.bot.scheduler import BotScheduler
from src.bot.screener import MomentumScreener
from src.bot.tradingview_screener import TradingViewScreener
from src.bot.signals.base import Signal
from src.bot.signals.orb import OpeningRangeBreakout
from src.bot.state.persistence import BotState
from src.bot.state.trade_ledger import TradeLedger
from src.bot.stream_handler import StreamHandler
from src.core.schwab_client import SchwabClient
from src.core.schwab_stream import SchwabStreamClient
from src.core.order_executor import OrderExecutor
from src.core.position_manager import EXTERNAL_STRATEGY, PositionManager
from src.risk.portfolio_limits import PortfolioLimits
from src.risk.position_sizer import PositionSizer


class TradingBot:
    """
    Momentum day trading bot controller.

    Strategy: Opening Range Breakout (ORB) on low-float stocks.
    Flow: Scanner -> Signal -> Risk Check -> Execute -> Monitor -> Exit
    Goal: One high-quality trade per day, 10% account growth.
    """

    def __init__(self, config: Optional[BotConfig] = None):
        """
        Initialize trading bot.

        Args:
            config: Bot configuration (uses default if None)
        """
        self.config = config or get_bot_config()

        # Schwab broker
        self.client = SchwabClient(
            app_key=self.config.schwab_app_key,
            app_secret=self.config.schwab_app_secret,
            callback_url=self.config.schwab_oauth_redirect_uri,
            token_path=self.config.schwab_token_path,
            pinned_account_hash=self.config.schwab_account_hash,
        )

        self.trade_ledger = TradeLedger(
            path="state/trades.json",
            starting_capital=400.0,
            goal=4000.0,
        )
        self.position_manager = PositionManager(trade_ledger=self.trade_ledger)

        # Order executor with dry-run mode
        self.order_executor = OrderExecutor(
            client=self.client,
            trading_mode=self.config.trading_mode,
        )

        # Risk components
        self.position_sizer = PositionSizer(
            max_position_risk_pct=self.config.max_position_risk_pct,
        )
        self.portfolio_limits = PortfolioLimits(
            max_drawdown_pct=self.config.max_drawdown_pct,
            max_daily_loss_pct=self.config.daily_loss_limit_pct,
            max_positions=self.config.max_positions,
            max_daily_trades=self.config.max_daily_trades,
        )

        # Float data provider (FMP key removed in Schwab migration; provider
        # handles None gracefully and skips the FMP fetch path).
        self.float_provider = FloatDataProvider(fmp_api_key=None)

        # TradingView screener (primary scanner, no API key required)
        self.tv_screener = TradingViewScreener() if self.config.use_tradingview_screener else None

        # Momentum scanner. Catalyst-news enrichment was dropped with the
        # tastytrade -> Schwab migration (Schwab has no news endpoint).
        self.momentum_scanner = MomentumScreener(
            float_provider=self.float_provider,
            client=self.client,
            news_enabled=False,
            tv_screener=self.tv_screener,
            use_tradingview=self.config.use_tradingview_screener,
        )

        # ORB strategy
        self.strategy = OpeningRangeBreakout(target_r=self.config.risk_reward_target)

        # State
        self.bot_state = BotState(self.config.bot_state_file)

        # Bot components
        self.processor = SignalProcessor(
            config=self.config,
            position_sizer=self.position_sizer,
            portfolio_limits=self.portfolio_limits,
        )
        self.executor = TradeExecutor(
            order_executor=self.order_executor,
            position_manager=self.position_manager,
            enable_orb_live=self.config.enable_orb_live,
        )
        self.monitor = PositionMonitor(
            client=self.client,
            position_manager=self.position_manager,
            strategies={"orb": self.strategy},
            trade_executor=self.executor,
        )

        # Stream client (Schwab WebSocket)
        self.ws_client = SchwabStreamClient(schwab_client=self.client)

        # Stream handler (event-driven signal engine)
        self.stream_handler = StreamHandler(
            strategy=self.strategy,
            processor=self.processor,
            executor=self.executor,
            monitor=self.monitor,
            position_manager=self.position_manager,
            portfolio_limits=self.portfolio_limits,
            bot_state=self.bot_state,
            client=self.client,
            ws_client=self.ws_client,
            config=self.config,
            strategies={"orb": self.strategy},
        )

        # Register WebSocket callbacks
        self.ws_client.on_bar(self.stream_handler.on_bar)
        self.ws_client.on_quote(self.stream_handler.on_quote)
        self.ws_client.on_trade_update(self.stream_handler.on_trade_update)

        # Scheduler (schedule-based market clock, no broker API needed)
        self.scheduler = BotScheduler(self.config)
        self.scheduler.set_callbacks(
            momentum_scan=self._run_momentum_scan,
            end_of_day=self._end_of_day_cleanup,
            daily_reset=self._daily_reset,
            or_lock=self._lock_opening_ranges,
        )

        # Day trading state
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._daily_trades_today = 0
        self._symbol_trade_counts: dict[str, int] = {}  # Per-symbol daily trade count
        # Share the symbol trade counts dict with stream handler (same reference)
        self.stream_handler._symbol_trade_counts = self._symbol_trade_counts
        self._scanner_results = []  # Latest scanner hits

    async def start(self) -> None:
        """Start the trading bot with WebSocket streaming."""
        logger.info("Starting momentum day trading bot (DXLink mode)...")
        logger.info(f"  Mode: {self.config.trading_mode.value}")
        logger.info(f"  ENABLE_ORB_LIVE: {self.config.enable_orb_live}")

        # Check authentication — start dashboard-only mode if not authenticated
        if not self.client.is_authenticated:
            logger.warning("NOT AUTHENTICATED — dashboard running in setup mode")
            logger.warning("Complete OAuth setup at the dashboard to start trading")
            self._running = True
            await self._shutdown_event.wait()
            return

        logger.info(f"  Schedule: 6:00 AM - 4:00 PM ET | Safety net: 3:55 PM ET")
        logger.info(f"  Max daily trades: {self.config.max_daily_trades}")
        logger.info(
            f"  Scanner: ${self.config.scanner_min_price}-${self.config.scanner_max_price}, "
            f"{self.config.scanner_min_change_pct}%+ change, "
            f"${self.config.scanner_min_dollar_volume/1000:.0f}K+ $vol"
        )
        logger.info(f"  Screener: TradingView")

        # 1. Initial sync with broker (REST, one-time)
        await self._sync_with_broker()

        # 1b. Clear stale signals from previous sessions
        stale_count = self.bot_state.clear_active_signals()
        if stale_count:
            logger.info(f"[STARTUP] Cleared {stale_count} stale signals from previous session")

        # 1c. Restore in-day ORB state from disk so an in-day restart keeps
        # the morning's locks (today's date only; stale files are ignored).
        self._load_orb_state()

        # 2. Initial scan (REST) to get watchlist
        await self._run_momentum_scan()

        # 3. Connect Schwab data stream. The account_activity (trade-update)
        # stream is intentionally not connected: dry_run fabricates fills
        # locally, and live mode polls get_orders inside OrderExecutor.
        # Sharing a single stream connection avoids the keepalive races
        # we hit when two loops were both awaiting handle_message().
        logger.info("[WS] Connecting to Schwab data stream...")
        data_ok = await self.ws_client.connect_data()
        logger.info(f"[WS] Data stream: {'OK' if data_ok else 'FAILED'}")

        # 4. Subscribe to scanner results + open positions
        scan_symbols = [c.symbol for c in self._scanner_results]
        pos_symbols = [p.symbol for p in self.position_manager.get_open_positions()]
        all_symbols = list(set(scan_symbols + pos_symbols))

        if all_symbols:
            await self.ws_client.subscribe(
                bars=all_symbols,
                quotes=all_symbols,
            )
            # Backfill 5-min bar history for stream handler
            for symbol in all_symbols:
                await self.stream_handler._backfill_bars(symbol)
            logger.info(f"[WS] Subscribed to {len(all_symbols)} symbols: {all_symbols}")

        # 5. Start scheduler (time-specific events only: EOD, daily reset)
        # NOTE: Momentum scan uses its own asyncio loop (more reliable than APScheduler cron)
        self.scheduler.start()
        self._running = True

        logger.info("Trading bot started (DXLink mode)")
        logger.info("Scheduled jobs:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job['name']}: next run {job['next_run']}")

        # 6. Run background loops as independent tasks
        # NOT asyncio.gather — the SDK's anyio cancel scopes leak CancelledError
        # which would cancel ALL tasks in a gather. Independent tasks are isolated.
        data_task = asyncio.create_task(self._resilient_data_loop())
        poll_task = asyncio.create_task(self._position_poll_loop())
        scan_task = asyncio.create_task(self._scan_loop())

        # Wait for shutdown signal (only thing in this coroutine)
        await self._shutdown_event.wait()

        # Clean shutdown: cancel the background tasks
        for task in [data_task, poll_task, scan_task]:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass

    async def _resilient_data_loop(self) -> None:
        """Pump the Schwab data stream, reconnecting on every disconnect.

        Schwab's WebSocket dies on keepalive timeout periodically; the
        previous version just slept and tried handle_message on the dead
        socket forever. Tear down and re-login with exponential backoff.
        """
        backoff = 5
        while not self._shutdown_event.is_set():
            try:
                logger.debug("[STREAM] Entering data loop...")
                await self.ws_client.run_data_loop()
                logger.warning("[STREAM] Data loop exited normally")
            except BaseException as e:
                if self._shutdown_event.is_set():
                    break
                logger.error(
                    f"[STREAM] Data loop crashed ({type(e).__name__}: {e}), "
                    f"reconnecting in {backoff}s..."
                )
                await asyncio.sleep(backoff)
                try:
                    ok = await self.ws_client.reconnect_data()
                    if ok:
                        logger.info("[STREAM] Reconnected successfully")
                        backoff = 5
                    else:
                        logger.error("[STREAM] Reconnect returned False")
                        backoff = min(backoff * 2, 60)
                except Exception as recon_err:
                    logger.error(f"[STREAM] Reconnect raised: {recon_err}")
                    backoff = min(backoff * 2, 60)

    async def _scan_loop(self) -> None:
        """
        Run momentum scanner every 5 minutes as a simple asyncio loop.

        More reliable than APScheduler cron — the scheduler's timer chain can break
        when the event loop is busy with DXLink streaming. This loop runs the scan
        immediately on startup, then every 5 minutes during trading hours (6AM-4PM ET).
        """
        import pytz

        ET = pytz.timezone("America/New_York")
        SCAN_INTERVAL = 300  # 5 minutes

        # Immediate scan on startup
        try:
            await self._run_momentum_scan()
        except Exception as e:
            logger.error(f"[SCAN] Startup scan error: {e}")

        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(SCAN_INTERVAL)
            except asyncio.CancelledError:
                break

            # Only scan during trading hours (6AM-4PM ET, weekdays)
            now_et = datetime.now(ET)
            if now_et.weekday() >= 5:
                continue
            hour = now_et.hour
            if hour < 6 or hour >= 16:
                continue

            try:
                await self._run_momentum_scan()
            except Exception as e:
                logger.error(f"[SCAN] Scan loop error: {e}")

    async def _position_poll_loop(self) -> None:
        """
        REST fallback for position monitoring.

        DXLink may not send quotes for illiquid stocks. This polls
        open positions every 30s via REST and runs exit checks,
        ensuring stops/targets fire even without streaming data.
        """
        import time as _time

        POLL_INTERVAL = 30  # seconds

        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(POLL_INTERVAL)

                positions = self.position_manager.get_open_positions()
                if not positions:
                    continue

                # Self-heal: drop any in-memory positions Schwab no longer
                # holds. The executor's _wait_for_fill historically
                # mis-reported a real fill as "failed", leaving the bot
                # in a state where it kept retrying the exit every 30s.
                # Reconciling here means a stale position falls off on
                # the next poll cycle even if individual exit attempts
                # keep racing the order-status API.
                try:
                    broker_symbols = {
                        p["symbol"] for p in self.client.get_positions()
                        if float(p.get("qty", 0)) > 0
                    }
                    stale = [p for p in positions if p.symbol not in broker_symbols]
                    if stale:
                        for sp in stale:
                            try:
                                exit_px = self.client.get_latest_price(sp.symbol)
                            except Exception:
                                exit_px = sp.current_price or sp.entry_price
                            self.position_manager.close_position(
                                sp.symbol, exit_px, "reconciled (broker closed)"
                            )
                            logger.info(
                                f"[POLL RECONCILE] {sp.symbol}: broker no longer "
                                f"holds this position; closed in PositionManager "
                                f"at ${exit_px:.2f}"
                            )
                        positions = self.position_manager.get_open_positions()
                        if not positions:
                            continue
                except Exception as recon_err:
                    logger.warning(
                        f"[POLL RECONCILE] reconciliation check failed: {recon_err}"
                    )

                for position in positions:
                    symbol = position.symbol
                    # Skip if we got a recent quote from DXLink (< 60s)
                    quote = self.stream_handler._latest_quotes.get(symbol)
                    if quote and quote.get("timestamp"):
                        try:
                            from datetime import datetime, timezone
                            qt = datetime.fromisoformat(
                                quote["timestamp"].replace("Z", "+00:00")
                            )
                            age = (datetime.now(timezone.utc) - qt).total_seconds()
                            if age < 60:
                                continue
                        except (ValueError, TypeError):
                            pass

                    # No recent streaming quote — poll via REST
                    try:
                        price = self.client.get_latest_price(symbol)
                    except Exception:
                        price = None

                    if price is None:
                        continue

                    position.update_price(price)
                    logger.debug(
                        f"[POLL] {symbol} REST price ${price:.2f} "
                        f"(no stream data)"
                    )

                    # Run exit checks at this price
                    exit_signal = await self.monitor.check_position_at_price(
                        symbol, price
                    )
                    if exit_signal:
                        logger.info(
                            f"[POLL EXIT] {symbol}: {exit_signal.reason} "
                            f"@ ${price:.2f}"
                        )
                        exec_result = await self.stream_handler.executor.execute_exit(
                            symbol=symbol,
                            reason=exit_signal.reason,
                        )
                        if exec_result.success:
                            pnl = (
                                exec_result.position.realized_pnl
                                if exec_result.position
                                else 0
                            )
                            logger.info(f"  Closed {symbol}: P&L ${pnl:.2f}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[POLL] Position poll error: {e}")
                await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the trading bot gracefully."""
        logger.info("Stopping trading bot...")

        self._running = False

        # Disconnect DXLink streams
        await self.ws_client.disconnect()
        logger.info("[DXLink] Disconnected")

        self.scheduler.stop()

        # Save state
        self.bot_state.save()

        logger.info("Trading bot stopped")
        self._shutdown_event.set()

    def request_shutdown(self) -> None:
        """Request bot shutdown (called from signal handler)."""
        asyncio.create_task(self.stop())

    # -- Core: Momentum Scan + Signal Generation --------------------------

    async def _run_momentum_scan(self) -> None:
        """
        Main momentum scanning loop.

        1. Check if we should scan (daily trade limit, position open)
        2. Run momentum scanner to find candidates
        3. For each candidate, fetch 5-min bars and generate signal
        4. On first valid signal, execute trade and stop scanning
        """
        if not self._running:
            return

        self.bot_state.update_job_timestamp("momentum_scan")

        # Check if we've hit daily trade limit
        if self._daily_trades_today >= self.config.max_daily_trades:
            logger.debug(
                f"Daily trade limit reached "
                f"({self._daily_trades_today}/{self.config.max_daily_trades})"
            )
            return

        # No new entries after 3:15 PM ET — too close to 3:55 PM safety net
        import pytz
        from datetime import time as _time
        if datetime.now(pytz.timezone("America/New_York")).time() >= _time(15, 15):
            logger.debug("No new entries after 3:15 PM ET")
            return

        # Check if we already have an open position
        open_positions = self.position_manager.get_open_positions()
        if len(open_positions) >= self.config.max_positions:
            logger.debug(
                f"Position limit reached "
                f"({len(open_positions)}/{self.config.max_positions})"
            )
            return

        # Get account info
        try:
            account = self.client.get_account()
            equity = float(account.get("equity", 0))
            buying_power = float(account.get("buying_power", 0))
            daytrade_count = int(account.get("daytrade_count", 0))
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return

        current_positions = len(open_positions)

        # Run the momentum scanner
        logger.info("[SCAN] Running momentum scanner...")
        try:
            candidates = self.momentum_scanner.scan(
                min_price=self.config.scanner_min_price,
                max_price=self.config.scanner_max_price,
                preferred_min_price=self.config.scanner_preferred_min_price,
                min_change_pct=self.config.scanner_min_change_pct,
                min_dollar_volume=self.config.scanner_min_dollar_volume,
                min_float_millions=self.config.scanner_min_float_millions,
                enable_float_filter=self.config.scanner_enable_float_filter,
                top_n=self.config.scanner_top_n,
            )
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            candidates = []

        self._scanner_results = candidates

        # Record first sighting of each candidate so forward returns can be
        # measured later. Observational only — nothing here trades, and a
        # failure must never break the scan loop.
        try:
            from src.lab.signal_log import append_hits

            added = append_hits(
                Path(self.config.state_dir) / "signal_log.json", candidates
            )
            if added:
                logger.info(f"[SCAN] recorded {len(added)} new signal(s) to signal_log.json")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[SCAN] signal log skipped: {e}")

        if not candidates:
            logger.info("[SCAN] No candidates found")
            return

        logger.info(f"[SCAN] Found {len(candidates)} candidates:")
        for c in candidates:
            float_str = f", float={c.float_shares / 1e6:.1f}M" if c.float_shares else ""
            news_str = f", NEWS: {c.news_headline[:50]}..." if c.has_catalyst and c.news_headline else ""
            logger.info(
                f"  {c.symbol}: ${c.price:.2f} ({c.change_pct:+.1f}%), "
                f"relVol={c.relative_volume:.1f}x{float_str}{news_str}"
            )

        candidate_symbols = [c.symbol for c in candidates]

        # Register watchlist with the ORB strategy so the dashboard reflects
        # them between 9:25 and 9:45:30 ET. lock_or auto-registers too, but
        # explicit register makes the watchlist visible pre-lock.
        for symbol in candidate_symbols:
            self.strategy.register(symbol)

        # Persist ORB state so an in-day restart keeps the morning's locks.
        self._save_orb_state()

        # Stream subscriptions must be the UNION of current scanner candidates,
        # every symbol the ORB strategy is tracking (which includes the 09:45:30
        # locked symbols), and open positions. The previous code only sent
        # candidate_symbols, which silently unsubscribed locked ORB symbols
        # as the scanner refreshed -- so DAIC could go above its OR high
        # without the bot ever seeing the bar that crossed it.
        locked_orb_symbols = list(self.strategy.state.keys())
        pos_symbols = [p.symbol for p in self.position_manager.get_open_positions()]
        watchlist = sorted(set(candidate_symbols + locked_orb_symbols + pos_symbols))
        await self.stream_handler.update_watchlist(watchlist)

        # For each candidate, try to generate a signal
        for candidate in candidates:
            symbol = candidate.symbol

            # Skip if we already have a position or active signal
            if self.position_manager.has_position(symbol):
                continue
            if self.bot_state.has_active_signal(symbol):
                continue

            # Generate signal from 5-min bars (catalyst boosts signal strength)
            gen_signal = await self._generate_signal(
                symbol, has_catalyst=candidate.has_catalyst
            )
            if gen_signal is None:
                continue

            # Inject catalyst metadata from scanner into signal
            gen_signal.metadata["has_catalyst"] = candidate.has_catalyst
            gen_signal.metadata["news_headline"] = candidate.news_headline
            gen_signal.metadata["news_count"] = candidate.news_count
            gen_signal.metadata["news_source"] = candidate.news_source

            # Process through risk checks and execute
            logger.info(
                f"[SIGNAL] {symbol}: {gen_signal.direction.value} "
                f"(strength={gen_signal.strength:.2f}, R:R={gen_signal.risk_reward_ratio:.1f})"
            )

            executed = await self._process_signal(
                gen_signal, equity, buying_power, current_positions, daytrade_count
            )

            if executed:
                self._daily_trades_today += 1
                self._symbol_trade_counts[symbol] = self._symbol_trade_counts.get(symbol, 0) + 1
                self.portfolio_limits.record_entry()
                logger.info(f"[TRADE] Trade #{self._daily_trades_today} executed for {symbol} (symbol trade #{self._symbol_trade_counts[symbol]})")

    async def _generate_signal(
        self, symbol: str, has_catalyst: bool = False
    ) -> Optional[Signal]:
        """
        Generate a signal for a symbol using 5-min bars.

        Tries pullback strategy first (higher quality setup), then
        falls back to surge strategy (catches initial moves).

        Args:
            symbol: Stock ticker
            has_catalyst: Whether the stock has a news catalyst

        Returns:
            Signal if entry conditions met, None otherwise
        """
        try:
            bars = self.client.get_bars(
                symbol,
                timeframe=self.config.stock_timeframe,
                limit=100,
            )
            if bars is None or len(bars) < 40:
                logger.debug(
                    f"[SIGNAL] {symbol}: insufficient bars "
                    f"({len(bars) if bars is not None else 0})"
                )
                return None

            current_price = self.client.get_latest_price(symbol)

            return self.strategy.generate(
                symbol, bars, current_price, has_catalyst=has_catalyst,
                symbol_trade_count=self._symbol_trade_counts.get(symbol, 0),
            )

        except Exception as e:
            logger.debug(f"[SIGNAL] {symbol}: error generating signal: {e}")
            return None

    async def _process_signal(
        self,
        signal: Signal,
        equity: float,
        buying_power: float,
        current_positions: int,
        daytrade_count: int = 0,
    ) -> bool:
        """
        Process a signal through validation and execution.

        Returns True if trade was executed successfully.
        """
        # Process through risk checks
        result = self.processor.process(
            signal=signal,
            account_equity=equity,
            buying_power=buying_power,
            current_positions=current_positions,
            daytrade_count=daytrade_count,
        )

        if not result.passed:
            logger.info(f"  Rejected: {result.rejection_reason}")
            self.bot_state.remove_active_signal(signal.symbol, executed=False)
            return False

        for warning in result.warnings:
            logger.warning(f"  {warning}")

        # Add to active signals
        self.bot_state.add_signal(signal)

        # Execute trade
        trade_params = result.trade_params
        logger.info(
            f"  Executing: {trade_params.quantity:.2f} shares of {signal.symbol} "
            f"@ ~${trade_params.entry_price:.2f} "
            f"(stop=${trade_params.stop_price:.2f}, target=${trade_params.target_price:.2f})"
        )

        exec_result = await self.executor.execute_entry(trade_params)

        if exec_result.success:
            logger.info(
                f"  FILLED: {exec_result.order_result.filled_qty:.2f} "
                f"@ ${exec_result.order_result.filled_price:.2f}"
            )
            self.bot_state.remove_active_signal(signal.symbol, executed=True)
            return True
        else:
            logger.error(f"  FAILED: {exec_result.error}")
            self.bot_state.remove_active_signal(signal.symbol, executed=False)
            return False

    # -- Position Monitoring ----------------------------------------------

    async def _monitor_positions(self) -> None:
        """Monitor positions for exit conditions."""
        if not self._running:
            return

        self.bot_state.update_job_timestamp("position_monitor")

        exit_signals = await self.monitor.check_all_positions()

        for exit_signal in exit_signals:
            symbol = exit_signal.symbol
            logger.info(f"[EXIT] {symbol}: {exit_signal.reason}")

            exec_result = await self.executor.execute_exit(
                symbol=symbol,
                reason=exit_signal.reason,
            )

            if exec_result.success:
                pnl = exec_result.position.realized_pnl if exec_result.position else 0
                logger.info(f"  Closed {symbol}: P&L ${pnl:.2f}")
            else:
                logger.error(f"  Failed to close {symbol}: {exec_result.error}")

    # -- End of Day -------------------------------------------------------

    async def _end_of_day_cleanup(self) -> None:
        """
        End-of-day cleanup: close all positions, cancel all orders.

        Called at 3:55 PM ET (safety net).
        """
        logger.info("[EOD] Running end-of-day cleanup...")

        # Cancel all pending orders
        try:
            cancelled = await self.executor.cancel_pending_orders()
            if cancelled:
                logger.info(f"[EOD] Cancelled {cancelled} pending orders")
        except Exception as e:
            logger.error(f"[EOD] Error cancelling orders: {e}")

        # Close open positions this bot opened. Lots opened outside the bot
        # (swing lots from scripts/live_swing.py, adopted via sync_with_broker)
        # are multi-day holds and must survive the intraday flatten.
        positions = self.position_manager.get_open_positions()
        for position in positions:
            if position.strategy == EXTERNAL_STRATEGY:
                logger.info(
                    f"[EOD] Skipping {position.symbol} — not ORB-owned "
                    f"(strategy={position.strategy})"
                )
                continue
            logger.info(f"[EOD] Closing {position.symbol} ({position.qty} shares)")
            exec_result = await self.executor.execute_exit(
                symbol=position.symbol,
                reason="end_of_day_cleanup",
            )
            if exec_result.success:
                pnl = exec_result.position.realized_pnl if exec_result.position else 0
                logger.info(f"  Closed: P&L ${pnl:.2f}")
            else:
                logger.error(f"  Failed: {exec_result.error}")

        logger.info("[EOD] Cleanup complete")

    async def _daily_reset(self) -> None:
        """
        Daily reset: clear counters, refresh state.

        Called at 6:00 AM ET before pre-market scanning starts.
        """
        logger.info("[RESET] Daily reset...")

        self._daily_trades_today = 0
        self._symbol_trade_counts.clear()  # .clear() to preserve shared reference
        self._scanner_results = []

        # Reset stream handler daily counters
        self.stream_handler.reset_daily()

        # Reset ORB strategy state for the new day, including the on-disk
        # snapshot so a daily reset can't be undone by a stale state file.
        self.strategy.reset()
        self._clear_orb_state()

        # Reset portfolio limits daily counters
        self.portfolio_limits.reset_daily_limits()

        # Sync fresh state from broker
        await self._sync_with_broker()

        logger.info("[RESET] Daily reset complete. Ready for pre-market scanning.")

    # -- Opening Range Lock -----------------------------------------------

    async def _lock_opening_ranges(self) -> None:
        """Fetch the 9:30-9:45 ET window for each watchlist symbol and lock OR.

        Filter explicitly by timestamp instead of using limit=N so the job
        works even if it fires late (e.g., scheduler retry after a restart).
        """
        if not self._running:
            return
        symbols = [c.symbol for c in self._scanner_results]
        symbols = list({s for s in symbols if s})
        if not symbols:
            logger.info("[OR-LOCK] No symbols on watchlist; skipping.")
            return

        import pytz
        _ET = pytz.timezone("America/New_York")
        today_et = datetime.now(_ET).date()
        or_start = _ET.localize(
            datetime.combine(today_et, dtime(9, 30))
        ).astimezone(timezone.utc)
        or_end = _ET.localize(
            datetime.combine(today_et, dtime(9, 45))
        ).astimezone(timezone.utc)

        # Schwab's pricehistory without explicit dates returns only the
        # most recent ~20 5-min bars (~95 min). Pass an explicit start/end
        # so this method works even when fired several hours after 09:45
        # (e.g., via the admin endpoint after a midday auth recovery).
        import pandas as pd
        for symbol in symbols:
            try:
                resp = self.client._client.get_price_history_every_five_minutes(
                    symbol,
                    start_datetime=or_start.astimezone(_ET).replace(tzinfo=None),
                    end_datetime=(or_end + pd.Timedelta(minutes=10))
                        .astimezone(_ET).replace(tzinfo=None),
                )
                if resp.status_code != 200:
                    logger.warning(
                        f"[OR-LOCK] {symbol}: pricehistory HTTP {resp.status_code}"
                    )
                    continue
                candles = resp.json().get("candles", [])
                if not candles:
                    logger.warning(f"[OR-LOCK] {symbol}: no bars returned")
                    continue
                bars = pd.DataFrame(candles)
                bars["timestamp"] = pd.to_datetime(bars["datetime"], unit="ms", utc=True)
                bars = bars.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
                or_bars = bars[(bars.index >= or_start) & (bars.index < or_end)]
                if or_bars.empty:
                    logger.warning(
                        f"[OR-LOCK] {symbol}: no bars in OR window "
                        f"{or_start.isoformat()} -> {or_end.isoformat()}"
                    )
                    continue
                self.strategy.lock_or(symbol, or_bars)
                st = self.strategy.state[symbol]
                logger.info(
                    f"[OR-LOCK] {symbol}: H=${st.or_high:.2f} L=${st.or_low:.2f} "
                    f"V={st.or_volume:,} (bars={len(or_bars)})"
                )
            except Exception as e:
                logger.error(f"[OR-LOCK] {symbol} failed: {e}")

        # Persist the freshly locked ORB state so a restart preserves it,
        # and write an immutable per-day snapshot for backtests/audit.
        self._save_orb_state()
        self._save_orb_history()

    # -- ORB state persistence --------------------------------------------

    def _orb_state_path(self) -> "Path":
        from pathlib import Path
        return Path(self.config.state_dir) / "orb_state.json"

    def _save_orb_state(self) -> None:
        """Write current ORB strategy state to disk so an in-day restart
        keeps the morning's locks. Atomic: tempfile + os.replace."""
        try:
            import os, json, tempfile, pytz
            ET = pytz.timezone("America/New_York")
            today = datetime.now(ET).date().isoformat()
            path = self._orb_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"date": today, "state": self.strategy.to_dict()}
            fd, tmp = tempfile.mkstemp(
                prefix=".orb_state.", dir=str(path.parent), text=True
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(payload, f)
                os.replace(tmp, str(path))
            except Exception:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                raise
        except Exception as e:
            logger.warning(f"[ORB] save state failed: {e}")

    def _load_orb_state(self) -> bool:
        """Restore ORB strategy state from disk if the file is from today."""
        path = self._orb_state_path()
        if not path.exists():
            return False
        try:
            import json, pytz
            ET = pytz.timezone("America/New_York")
            today = datetime.now(ET).date().isoformat()
            with open(path) as f:
                data = json.load(f)
            file_date = data.get("date")
            if file_date != today:
                logger.info(
                    f"[ORB] state file is from {file_date}, today is {today}; "
                    f"ignoring (will be cleared on daily reset)"
                )
                return False
            self.strategy.load_state(data.get("state", {}))
            n = len(self.strategy.state)
            locked = sum(1 for st in self.strategy.state.values() if st.or_locked)
            logger.info(f"[ORB] restored state from disk: {n} symbols ({locked} locked)")
            return True
        except Exception as e:
            logger.warning(f"[ORB] load state failed: {e}")
            return False

    def _clear_orb_state(self) -> None:
        try:
            self._orb_state_path().unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"[ORB] clear state failed: {e}")

    def _save_orb_history(self) -> None:
        """Write an immutable per-day snapshot of the locked ORB state.

        Backtests read these to reproduce real scanner picks day-by-day
        rather than re-running today's picks against historical bars.
        Files live at state/orb_history/YYYY-MM-DD.json.
        """
        try:
            import os, json, pytz
            from pathlib import Path
            ET = pytz.timezone("America/New_York")
            today = datetime.now(ET).date().isoformat()
            hist_dir = Path(self.config.state_dir) / "orb_history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            path = hist_dir / f"{today}.json"
            payload = {"date": today, "state": self.strategy.to_dict()}
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            n = len(payload["state"])
            logger.info(f"[ORB] wrote history snapshot {path.name} ({n} symbols)")
        except Exception as e:
            logger.warning(f"[ORB] save history failed: {e}")

    # -- Broker Sync ------------------------------------------------------

    async def _sync_with_broker(self) -> None:
        """Sync positions and account with broker."""
        try:
            account = self.client.get_account()
            self._account_snapshot = account
            equity = float(account.get("equity", 0))
            buying_power = float(account.get("buying_power", 0))
            try:
                orders = self.client.get_orders(status="open")
                if isinstance(orders, list):
                    self._open_orders_snapshot = orders
            except Exception:
                pass

            # Update risk components
            self.portfolio_limits.update_equity(equity)

            # Get broker positions
            broker_positions = self.client.get_positions()

            # Track which positions exist before sync
            existing_symbols = set(self.position_manager.get_symbols())

            # Sync with position manager
            self.position_manager.sync_with_broker(
                broker_positions=[
                    {
                        "symbol": p["symbol"],
                        "qty": float(p["qty"]),
                        "avg_entry_price": float(p["avg_entry_price"]),
                        "current_price": float(p["current_price"]),
                    }
                    for p in broker_positions
                ],
                equity=equity,
            )

            # Add default stops to newly synced positions
            for bp in broker_positions:
                symbol = bp["symbol"]
                if symbol not in existing_symbols:
                    await self._add_default_stops(symbol)

            # Reconcile broker stop orders with open positions
            self._reconcile_broker_stops()

            self.bot_state.update_job_timestamp("broker_sync")

            logger.info(
                f"[SYNC] ${equity:.2f} equity, ${buying_power:.2f} BP, "
                f"{len(broker_positions)} positions"
            )

        except Exception as e:
            logger.error(f"Broker sync error: {e}")

    async def _add_default_stops(self, symbol: str) -> None:
        """Add default stop-loss and take-profit to a broker-synced position.

        No-op when ORB live is off — swing positions must not get 5-min ATR
        take-profits that amputate the right tail.
        """
        if not bool(getattr(self.config, "enable_orb_live", False)):
            return
        position = self.position_manager.get_position(symbol)
        if not position:
            return


        try:
            # Get 5-min bars to calculate ATR
            bars = self.client.get_bars(symbol, timeframe="5Min", limit=20)

            if bars is None or len(bars) < 14:
                # Fallback: percentage-based stops for day trading
                stop_pct = 0.05
                position.stop_loss = position.entry_price * (1 - stop_pct)
                position.initial_stop_loss = position.stop_loss
                position.take_profit = position.entry_price * (1 + stop_pct * self.config.risk_reward_target)
                position.trailing_stop_pct = None
                self.executor._place_broker_stop(position)
                logger.info(f"  Added default stops for {symbol} (5% fallback)")
                return

            # Calculate ATR for dynamic stops
            from src.data.indicators import atr
            atr_value = atr(bars["high"], bars["low"], bars["close"], period=14).iloc[-1]

            stop_mult = self.config.stock_atr_stop_multiplier

            # Calculate stop and target (LONG only for momentum strategy)
            rr_target = self.config.risk_reward_target
            from src.core.position_manager import PositionSide
            if position.side == PositionSide.LONG:
                position.stop_loss = position.entry_price - (atr_value * stop_mult)
                position.take_profit = position.entry_price + (atr_value * stop_mult * rr_target)
            else:
                position.stop_loss = position.entry_price + (atr_value * stop_mult)
                position.take_profit = position.entry_price - (atr_value * stop_mult * rr_target)

            # Set initial_stop_loss for R-based trailing
            position.initial_stop_loss = position.stop_loss

            # Progressive R-based trailing handles exits now (no % trailing)
            position.trailing_stop_pct = None

            self.executor._place_broker_stop(position)
            logger.info(
                f"  Added ATR stops for {symbol}: "
                f"SL=${position.stop_loss:.2f}, TP=${position.take_profit:.2f}"
            )

        except Exception as e:
            logger.warning(f"  Could not add stops for {symbol}: {e}")

    def _reconcile_broker_stops(self) -> None:
        """
        Reconcile broker stop orders with open positions on startup.

        - If a position has no broker stop, place one.
        - If a stop order exists at the broker for a position, record its ID.
        - If a stop order filled while the bot was down, the position was
          already closed by sync_with_broker (position disappeared from broker).
        """
        try:
            open_orders = self.order_executor.get_open_orders()
            # Index stop orders by symbol
            stop_orders_by_symbol: dict[str, dict] = {}
            for order in open_orders:
                if order.get("type") == "stop_limit":
                    sym = order.get("symbol", "")
                    stop_orders_by_symbol[sym] = order

            for position in self.position_manager.get_open_positions():
                existing_stop = stop_orders_by_symbol.get(position.symbol)
                if existing_stop:
                    # Found an existing broker stop — adopt it
                    position.broker_stop_order_id = existing_stop["id"]
                    logger.info(
                        f"[RECONCILE] {position.symbol}: adopted existing broker "
                        f"stop (order={existing_stop['id']})"
                    )
                elif position.stop_loss is not None:
                    # No broker stop — place one
                    self.executor._place_broker_stop(position)
                    logger.info(
                        f"[RECONCILE] {position.symbol}: placed missing broker "
                        f"stop @ ${position.stop_loss:.2f}"
                    )
        except Exception as e:
            logger.error(f"[RECONCILE] Broker stop reconciliation error: {e}")

    # -- Health Check -----------------------------------------------------

    async def health_check(self) -> dict:
        """Get bot health status."""
        try:
            account = self.client.get_account()
            equity = float(account.get("equity", 0))
            buying_power = float(account.get("buying_power", 0))
        except Exception:
            equity = 0
            buying_power = 0

        return {
            "running": self._running,
            "scheduler_running": self.scheduler.is_running,
            "is_trading_day": self.scheduler.is_trading_day(),
            "in_premarket": self.scheduler.is_in_premarket(),
            "market_open": self.scheduler.is_market_open(),
            "account": {
                "equity": equity,
                "buying_power": buying_power,
            },
            "day_trading": {
                "trades_today": self._daily_trades_today,
                "max_daily_trades": self.config.max_daily_trades,
                "scanner_hits": len(self._scanner_results),
            },
            "tradingview": {
                "enabled": self.config.use_tradingview_screener,
                "last_query": (
                    self.tv_screener.last_query_time.isoformat()
                    if self.tv_screener and self.tv_screener.last_query_time
                    else None
                ),
            },
            "websocket": self.ws_client.get_status(),
            "stream": self.stream_handler.get_status(),
            "scanner_results": [
                {
                    "symbol": c.symbol,
                    "price": c.price,
                    "change_pct": c.change_pct,
                    "relative_volume": c.relative_volume,
                    "float_millions": c.float_shares / 1e6 if c.float_shares else None,
                    "passes_all": c.passes_all_filters,
                }
                for c in self._scanner_results[:10]
            ],
            "positions": self.monitor.get_positions_summary(),
            "state": self.bot_state.get_state_summary(),
            "jobs": self.scheduler.get_jobs(),
        }


def setup_signal_handlers(bot: TradingBot) -> None:
    """Setup signal handlers for graceful shutdown."""

    def handle_signal(signum, frame):
        logger.warning(f"Received signal {signum}, shutting down...")
        bot.request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


async def run_bot(config: Optional[BotConfig] = None) -> None:
    """Run the trading bot."""
    bot = TradingBot(config)
    setup_signal_handlers(bot)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(run_bot())
