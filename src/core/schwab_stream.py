"""SchwabStreamClient + BarAggregator.

This file gets the full StreamClient wrapper in Task 12. Task 11 only
adds the BarAggregator that rolls 1-min OHLCV bars into N-min bars.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from loguru import logger


def _invoke_cb(cb, payload, *, label: str) -> None:
    """Call a stream callback. If it returns a coroutine, schedule it so
    async handlers (StreamHandler.on_bar/on_quote/on_trade_update are all
    async) are actually awaited."""
    try:
        result = cb(payload)
    except Exception as e:
        logger.error(f"[STREAM] {label} callback raised: {e}")
        return
    if inspect.iscoroutine(result):
        try:
            asyncio.get_running_loop().create_task(result)
        except RuntimeError:
            logger.error(
                f"[STREAM] {label} callback is async but no running event "
                "loop -- coroutine dropped"
            )

try:
    from schwab.streaming import StreamClient
except ImportError:  # pragma: no cover
    StreamClient = None


@dataclass
class _Window:
    start_minute: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarAggregator:
    """
    Roll N 1-minute OHLCV bars into a single window-minute bar.

    Each window starts at minute % window_minutes == 0 and closes when a 1-min
    bar arrives whose floored window-start is greater than the current window's
    start. The completed window is emitted via on_emit(bar_dict).
    """

    def __init__(self, *, window_minutes: int, on_emit: Callable[[dict], None]):
        self._window = window_minutes
        self._on_emit = on_emit
        self._open_windows: dict[str, _Window] = {}

    @staticmethod
    def _floor_minute(ts_iso: str, window: int) -> tuple[datetime, int]:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        floored = dt.minute - (dt.minute % window)
        return dt.replace(minute=floored, second=0, microsecond=0), floored

    def feed(self, bar: dict) -> None:
        symbol = bar["symbol"]
        floor_dt, floor_min = self._floor_minute(bar["timestamp"], self._window)
        floor_key = int(floor_dt.timestamp())

        win = self._open_windows.get(symbol)
        if win is None or win.start_minute != floor_key:
            if win is not None:
                self._on_emit({
                    "symbol": symbol,
                    "timestamp": datetime.fromtimestamp(win.start_minute, tz=floor_dt.tzinfo).isoformat(),
                    "open": win.open, "high": win.high, "low": win.low, "close": win.close,
                    "volume": win.volume,
                })
            self._open_windows[symbol] = _Window(
                start_minute=floor_key,
                open=bar["open"], high=bar["high"], low=bar["low"],
                close=bar["close"], volume=bar["volume"],
            )
            return

        win.high = max(win.high, bar["high"])
        win.low = min(win.low, bar["low"])
        win.close = bar["close"]
        win.volume += bar["volume"]


class SchwabStreamClient:
    def __init__(self, *, schwab_client):
        self._schwab = schwab_client
        self._stream: Optional["StreamClient"] = None
        self._bar_callbacks: List[Callable] = []
        self._quote_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []
        self._aggregator = BarAggregator(
            window_minutes=5,
            on_emit=self._dispatch_bar_to_callbacks,
        )
        self._subscribed = {"bars": set(), "quotes": set()}
        self._data_connected = False
        self._trade_connected = False

    # -- Callback registration --------------------------------------------
    def on_bar(self, cb: Callable) -> None:
        self._bar_callbacks.append(cb)

    def on_quote(self, cb: Callable) -> None:
        self._quote_callbacks.append(cb)

    def on_trade_update(self, cb: Callable) -> None:
        self._trade_callbacks.append(cb)

    # -- Connection -------------------------------------------------------
    async def connect_data(self) -> bool:
        try:
            self._stream = StreamClient(self._schwab._client)
            await self._stream.login()
            self._stream.add_chart_equity_handler(self._handle_chart_equity)
            self._stream.add_level_one_equity_handler(self._handle_quote)
            self._data_connected = True
            return True
        except Exception as e:
            logger.error(f"[STREAM] connect_data failed: {e}")
            return False

    async def connect_trades(self) -> bool:
        try:
            if self._stream is None:
                self._stream = StreamClient(self._schwab._client)
                await self._stream.login()
            self._stream.add_account_activity_handler(self._handle_trade_update)
            await self._stream.account_activity_sub()
            self._trade_connected = True
            return True
        except Exception as e:
            logger.error(f"[STREAM] connect_trades failed: {e}")
            return False

    # -- Subscriptions ----------------------------------------------------
    async def subscribe(self, *, bars: List[str] = (), quotes: List[str] = ()) -> None:
        if bars:
            await self._stream.chart_equity_subs(list(bars))
            self._subscribed["bars"].update(bars)
        if quotes:
            await self._stream.level_one_equity_subs(list(quotes))
            self._subscribed["quotes"].update(quotes)

    async def unsubscribe(self, *, bars: List[str] = (), quotes: List[str] = ()) -> None:
        if bars:
            await self._stream.chart_equity_unsubs(list(bars))
            self._subscribed["bars"].difference_update(bars)
        if quotes:
            await self._stream.level_one_equity_unsubs(list(quotes))
            self._subscribed["quotes"].difference_update(quotes)

    async def update_subscriptions(self, *, bars: List[str], quotes: List[str]) -> None:
        cur_bars = self._subscribed["bars"]
        cur_quotes = self._subscribed["quotes"]
        new_bars = set(bars) - cur_bars
        drop_bars = cur_bars - set(bars)
        new_quotes = set(quotes) - cur_quotes
        drop_quotes = cur_quotes - set(quotes)
        if new_bars or new_quotes:
            await self.subscribe(bars=list(new_bars), quotes=list(new_quotes))
        if drop_bars or drop_quotes:
            await self.unsubscribe(bars=list(drop_bars), quotes=list(drop_quotes))

    # -- Status -----------------------------------------------------------
    @property
    def data_connected(self) -> bool:
        return self._data_connected

    @property
    def trade_connected(self) -> bool:
        return self._trade_connected

    @property
    def subscribed_symbols(self) -> dict:
        return {k: set(v) for k, v in self._subscribed.items()}

    def get_status(self) -> dict:
        return {
            "data_connected": self._data_connected,
            "trade_connected": self._trade_connected,
            "subscribed_bars": sorted(self._subscribed["bars"]),
            "subscribed_quotes": sorted(self._subscribed["quotes"]),
        }

    # -- Loops ------------------------------------------------------------
    async def run_data_loop(self) -> None:
        if self._stream is None:
            raise RuntimeError("connect_data() must be called first")
        while True:
            await self._stream.handle_message()

    async def run_trade_loop(self) -> None:
        if self._stream is None:
            raise RuntimeError("connect_trades() must be called first")
        while True:
            await self._stream.handle_message()

    async def disconnect(self) -> None:
        if self._stream is not None:
            try:
                await self._stream.logout()
            except Exception:
                pass
        self._stream = None
        self._data_connected = False
        self._trade_connected = False
        self._subscribed = {"bars": set(), "quotes": set()}

    async def reconnect_data(self) -> bool:
        """Tear down a dead stream, log back in, and restore subscriptions.

        Schwab's WebSocket dies on keepalive timeout fairly regularly. The
        upstream loop calls this on any handle_message exception so the
        bot keeps receiving bars across disconnects.
        """
        saved_bars = list(self._subscribed.get("bars", set()))
        saved_quotes = list(self._subscribed.get("quotes", set()))
        await self.disconnect()
        ok = await self.connect_data()
        if not ok:
            return False
        if saved_bars or saved_quotes:
            await self.subscribe(bars=saved_bars, quotes=saved_quotes)
            logger.info(
                f"[STREAM] Reconnected; resubscribed bars={len(saved_bars)} "
                f"quotes={len(saved_quotes)}"
            )
        return True

    # -- Internal handlers (Schwab → our callback shape) -----------------
    def _handle_chart_equity(self, msg: dict) -> None:
        for content in msg.get("content", []):
            try:
                # Schwab's CHART_EQUITY service sends the timestamp as
                # CHART_TIME_MILLIS (milliseconds since epoch). Earlier
                # guesses at the field name silently dropped every bar.
                ts_ms = (
                    content.get("CHART_TIME_MILLIS")
                    or content.get("CHART_TIME")
                    or content.get("3")
                )
                if ts_ms is None:
                    logger.debug(
                        f"[STREAM] chart_equity missing timestamp; skipping: {content}"
                    )
                    continue
                self._aggregator.feed({
                    "symbol": content["key"],
                    "timestamp": _ms_to_iso(ts_ms),
                    "open": float(content.get("OPEN_PRICE") or content.get("4") or 0),
                    "high": float(content.get("HIGH_PRICE") or content.get("5") or 0),
                    "low": float(content.get("LOW_PRICE") or content.get("6") or 0),
                    "close": float(content.get("CLOSE_PRICE") or content.get("7") or 0),
                    "volume": int(content.get("VOLUME") or content.get("8") or 0),
                })
            except Exception as e:
                logger.warning(
                    f"[STREAM] bad chart_equity content {content!r}: {e}"
                )

    def _dispatch_bar_to_callbacks(self, bar: dict) -> None:
        for cb in self._bar_callbacks:
            _invoke_cb(cb, bar, label="bar")

    def _handle_quote(self, msg: dict) -> None:
        for content in msg.get("content", []):
            quote = {
                "symbol": content["key"],
                "bid": float(content.get("BID_PRICE") or content.get("1") or 0),
                "ask": float(content.get("ASK_PRICE") or content.get("2") or 0),
                "last": float(content.get("LAST_PRICE") or content.get("3") or 0),
                "timestamp": _now_iso(),
            }
            for cb in self._quote_callbacks:
                _invoke_cb(cb, quote, label="quote")

    def _handle_trade_update(self, msg: dict) -> None:
        for cb in self._trade_callbacks:
            _invoke_cb(cb, msg, label="trade")


def _ms_to_iso(ms) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()
