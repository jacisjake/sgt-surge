"""
Momentum Pullback Strategy for day trading.

Implements Ross Cameron's approach:
- Trade low-float, high-volume momentum stocks ($1-$10, prefer $2+)
- Enter on the first pullback after an initial surge
- Price must be above VWAP (institutional fair value = trend is bullish)
- Volume confirmation: green candle volume > pullback avg volume
- Target: 2x risk/reward, tight trailing stop

Entry conditions (all must pass):
1. Price above VWAP (trading above institutional fair value)
2. Pullback detected: surge → 2-15 lower/red candles → first green new high
3. Pullback retracement < 65% of surge height
4. Entry candle volume >= average of pullback candles
5. Stop below pullback low (or 1.5× ATR, whichever is tighter)
6. Target = stop distance × risk_reward_target

Exit conditions:
1. 2 consecutive bar closes below VWAP (momentum fading)
2. Stop/target/trailing handled by position monitor
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.bot.signals.base import Signal, SignalDirection, SignalGenerator
from src.bot.signals.momentum_surge import calculate_vwap
from src.data.indicators import atr as calc_atr

logger = logging.getLogger(__name__)


class MomentumPullbackStrategy(SignalGenerator):
    """
    Ross Cameron-style momentum pullback strategy on 5-min bars.

    Looks for stocks that have surged (already up big on the day),
    waits for a pullback, and enters on the first candle that makes
    a new high after the pullback — but ONLY when price is above VWAP.
    """

    def __init__(
        self,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        pullback_min_candles: int = 2,
        pullback_max_candles: int = 8,
        pullback_max_retracement: float = 0.50,
        volume_entry_multiplier: float = 1.0,
        risk_reward_target: float = 2.0,
        min_signal_strength: float = 0.5,
        # Accept but ignore legacy MACD params for backward compat
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ):
        super().__init__(name="momentum_pullback")
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.pullback_min_candles = pullback_min_candles
        self.pullback_max_candles = pullback_max_candles
        self.pullback_max_retracement = pullback_max_retracement
        self.volume_entry_multiplier = volume_entry_multiplier
        self.risk_reward_target = risk_reward_target
        self.min_signal_strength = min_signal_strength

        # VWAP needs 1 bar, ATR needs atr_period, pullback detection needs 40
        self.min_bars = max(atr_period + 10, 40)

    def generate(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_price: Optional[float] = None,
        has_catalyst: bool = False,
    ) -> Optional[Signal]:
        """
        Generate a momentum pullback entry signal.

        Args:
            symbol: Stock symbol
            bars: 5-min OHLCV DataFrame
            current_price: Current price (uses last close if not provided)
            has_catalyst: Whether the stock has a news catalyst (boosts signal strength)

        Returns:
            Signal if pullback entry conditions are met, None otherwise
        """
        # Validate input
        if not self.validate_bars(bars, self.min_bars):
            return None

        bars = self.normalize_bars(bars)
        price = current_price or float(bars["close"].iloc[-1])

        # ── Step 1: Calculate VWAP ──────────────────────────────────────
        vwap_values = calculate_vwap(bars)
        cur_vwap = float(vwap_values.iloc[-1])

        # ── Step 2: Price must be above VWAP ─────────────────────────────
        if np.isnan(cur_vwap) or cur_vwap <= 0:
            logger.debug(f"[{symbol}] VWAP unavailable")
            return None

        if price <= cur_vwap:
            logger.debug(
                f"[{symbol}] price ${price:.2f} below VWAP ${cur_vwap:.2f}, skip"
            )
            return None

        price_vs_vwap = (price - cur_vwap) / cur_vwap

        # ── Step 3: Detect pullback pattern ────────────────────────────
        pullback = self._detect_pullback(bars)
        if pullback is None:
            return None

        surge_high = pullback["surge_high"]
        pullback_low = pullback["pullback_low"]
        pullback_candles = pullback["pullback_candle_count"]
        surge_start = pullback["surge_start_price"]

        logger.info(
            f"[{symbol}] Pullback detected: surge ${surge_start:.2f}→${surge_high:.2f}, "
            f"pullback to ${pullback_low:.2f} ({pullback_candles} candles), "
            f"new high forming"
        )

        # ── Step 4: Volume confirmation ────────────────────────────────
        # Entry candle should have more volume than the average pullback candle
        entry_volume = float(bars["volume"].iloc[-1])
        pullback_start_idx = -(pullback_candles + 1)
        pullback_end_idx = -1
        pullback_volumes = bars["volume"].iloc[pullback_start_idx:pullback_end_idx]

        if len(pullback_volumes) > 0:
            avg_pullback_volume = float(pullback_volumes.mean())
        else:
            avg_pullback_volume = entry_volume

        volume_ratio = entry_volume / avg_pullback_volume if avg_pullback_volume > 0 else 0

        if volume_ratio < self.volume_entry_multiplier:
            logger.debug(
                f"[{symbol}] Entry volume too low: "
                f"{volume_ratio:.1f}x vs required {self.volume_entry_multiplier}x"
            )
            return None

        # ── Step 5: Calculate stop and target ──────────────────────────
        atr_value = float(calc_atr(
            bars["high"], bars["low"], bars["close"],
            period=self.atr_period,
        ).iloc[-1])

        # Stop: below pullback low or ATR-based, whichever is tighter
        atr_stop = price - (atr_value * self.atr_stop_multiplier)
        pullback_stop = pullback_low - (atr_value * 0.25)  # Small buffer below pullback low

        # Use the tighter (higher) stop for day trading
        stop_price = max(atr_stop, pullback_stop)

        # Safety: stop must be below current price
        if stop_price >= price:
            stop_price = price * 0.97  # Fallback: 3% stop

        stop_distance = price - stop_price

        # Target: risk_reward_target × stop distance above entry
        target_price = price + (stop_distance * self.risk_reward_target)

        # ── Step 6: Calculate signal strength ──────────────────────────
        strength = self._calculate_strength(
            price_vs_vwap=price_vs_vwap,
            volume_ratio=volume_ratio,
            pullback_depth_pct=pullback["retracement_pct"],
            has_catalyst=has_catalyst,
        )

        if strength < self.min_signal_strength:
            logger.debug(f"[{symbol}] Signal strength too low: {strength:.2f}")
            return None

        # ── Step 7: Generate signal ────────────────────────────────────
        signal = Signal(
            symbol=symbol,
            direction=SignalDirection.LONG,
            strength=strength,
            entry_price=price,
            stop_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            strategy=self.name,
            timeframe="5Min",
            metadata={
                "system": "momentum_pullback",
                "vwap": round(cur_vwap, 4),
                "price_vs_vwap": round(price_vs_vwap * 100, 2),
                "atr": round(atr_value, 4),
                "surge_high": round(surge_high, 2),
                "pullback_low": round(pullback_low, 2),
                "pullback_candles": pullback_candles,
                "retracement_pct": round(pullback["retracement_pct"] * 100, 1),
                "volume_ratio": round(volume_ratio, 1),
                "stop_distance": round(stop_distance, 2),
                "risk_reward": round(self.risk_reward_target, 1),
            },
        )

        logger.info(
            f"[{symbol}] SIGNAL: LONG @ ${price:.2f}, "
            f"stop=${stop_price:.2f}, target=${target_price:.2f}, "
            f"strength={strength:.2f}, R:R={self.risk_reward_target:.1f}"
        )

        return signal

    def should_exit(
        self,
        symbol: str,
        bars: pd.DataFrame,
        entry_price: float,
        direction: SignalDirection,
        current_price: Optional[float] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if momentum has died and position should exit.

        Exit when 2 consecutive bar closes below VWAP (momentum fading).

        Note: Stop-loss, take-profit, and trailing stop are handled by
        the PositionMonitor separately.
        """
        if not self.validate_bars(bars, self.min_bars):
            return False, None

        bars = self.normalize_bars(bars)
        close = bars["close"]

        # VWAP exit — 2 consecutive bar closes below VWAP
        vwap_values = calculate_vwap(bars)
        if len(close) >= 2:
            cur_close = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            cur_vwap = float(vwap_values.iloc[-1])
            prev_vwap = float(vwap_values.iloc[-2])
            if (
                not np.isnan(cur_vwap) and not np.isnan(prev_vwap)
                and cur_close < cur_vwap and prev_close < prev_vwap
            ):
                return True, (
                    f"2 bars below VWAP "
                    f"(${prev_close:.2f}<${prev_vwap:.2f}, ${cur_close:.2f}<${cur_vwap:.2f})"
                )

        return False, None

    def _detect_pullback(self, bars: pd.DataFrame) -> Optional[dict]:
        """
        Detect a pullback pattern in the price action.

        A valid pullback:
        1. Price surged to a local high (the "surge peak")
        2. Price pulled back (2-15 candles of lower highs or red candles)
        3. Current candle is making a new high above recent pullback highs
        4. Pullback didn't retrace more than 65% of the surge

        Args:
            bars: Normalized OHLCV DataFrame

        Returns:
            Dict with pullback details, or None if no valid pullback found
        """
        lookback = 40  # Look back 40 candles (~3.3 hours on 5-min bars)
        if len(bars) < lookback:
            lookback = len(bars)

        recent = bars.iloc[-lookback:]
        highs = recent["high"].values
        lows = recent["low"].values
        closes = recent["close"].values
        opens = recent["open"].values

        # Find the local high (surge peak) in the lookback window
        # Skip the last candle (potential entry candle)
        peak_idx_relative = 0
        peak_high = 0.0

        for i in range(len(highs) - 2, max(0, len(highs) - lookback), -1):
            if highs[i] > peak_high:
                peak_high = highs[i]
                peak_idx_relative = i

        if peak_high == 0:
            return None

        # The current candle
        current_high = float(highs[-1])
        current_close = float(closes[-1])
        current_open = float(opens[-1])

        # Count pullback candles between peak and current
        # A pullback candle: high < previous high (lower highs) OR red candle
        pullback_candles = 0
        pullback_low = float("inf")
        pullback_highs = []

        for i in range(peak_idx_relative + 1, len(highs) - 1):
            candle_high = float(highs[i])
            candle_low = float(lows[i])
            candle_close = float(closes[i])
            candle_open = float(opens[i])

            # Is this a pullback candle? (lower high or red)
            is_lower_high = candle_high < peak_high
            is_red = candle_close < candle_open

            if is_lower_high or is_red:
                pullback_candles += 1
                pullback_low = min(pullback_low, candle_low)
                pullback_highs.append(candle_high)

        # Validate pullback length
        if pullback_candles < self.pullback_min_candles:
            logger.debug(f"Pullback too short: {pullback_candles} < {self.pullback_min_candles}")
            return None

        if pullback_candles > self.pullback_max_candles:
            logger.debug(f"Pullback too long: {pullback_candles} > {self.pullback_max_candles}")
            return None

        # Current candle must make a new high vs recent pullback candle highs
        # Use last 3 pullback highs instead of ALL — a single spike candle in
        # the pullback shouldn't disqualify the entire pattern
        recent_pullback_highs = pullback_highs[-3:] if len(pullback_highs) > 3 else pullback_highs
        if recent_pullback_highs and current_high <= max(recent_pullback_highs):
            logger.debug("Current candle not making new high above recent pullback")
            return None

        # Current candle should be green (bullish)
        if current_close <= current_open:
            logger.debug("Current candle is red, need green for entry")
            return None

        # Calculate surge start (the low before the peak)
        surge_start = float("inf")
        for i in range(max(0, peak_idx_relative - 10), peak_idx_relative):
            surge_start = min(surge_start, float(lows[i]))

        if surge_start == float("inf"):
            surge_start = float(lows[max(0, peak_idx_relative - 1)])

        # Calculate retracement
        surge_height = peak_high - surge_start
        if surge_height <= 0:
            return None

        pullback_depth = peak_high - pullback_low
        retracement_pct = pullback_depth / surge_height

        if retracement_pct > self.pullback_max_retracement:
            logger.debug(
                f"Pullback too deep: {retracement_pct:.1%} > "
                f"{self.pullback_max_retracement:.1%}"
            )
            return None

        return {
            "surge_high": peak_high,
            "surge_start_price": surge_start,
            "surge_height": surge_height,
            "pullback_low": pullback_low,
            "pullback_candle_count": pullback_candles,
            "retracement_pct": retracement_pct,
            "is_first_new_high": True,
        }

    def _calculate_strength(
        self,
        price_vs_vwap: float,
        volume_ratio: float,
        pullback_depth_pct: float,
        has_catalyst: bool = False,
    ) -> float:
        """Calculate signal strength (0.0 to 1.0)."""
        strength = 0.5  # Base strength

        # VWAP deviation: price well above VWAP = strong trend
        if price_vs_vwap > 0.02:
            strength += 0.15
        elif price_vs_vwap > 0.01:
            strength += 0.10

        # Volume: higher relative volume on entry = more conviction
        if volume_ratio >= 3.0:
            strength += 0.15
        elif volume_ratio >= 2.0:
            strength += 0.10
        elif volume_ratio >= 1.5:
            strength += 0.05

        # Shallow pullback = momentum still strong
        if pullback_depth_pct < 0.25:
            strength += 0.10
        elif pullback_depth_pct < 0.35:
            strength += 0.05

        # News catalyst
        if has_catalyst:
            strength += 0.10

        return max(0.0, min(1.0, strength))
