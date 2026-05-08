"""SchwabStreamClient + BarAggregator.

This file gets the full StreamClient wrapper in Task 12. Task 11 only
adds the BarAggregator that rolls 1-min OHLCV bars into N-min bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable


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
