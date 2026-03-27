"""
Momentum Surge Strategy for day trading.

Enters on the initial surge — no pullback required. For stocks that are
actively breaking out with strong momentum, volume, and VWAP confirmation.
Highly selective — fires rarely on the strongest setups only.

Trade-count-aware entry logic:
- 1st trade: Must be near daily HOD (within 5%) — the real surge
- 2nd trade: HOD filter relaxed, but requires stronger momentum (3x volume, 3% ROC)
- 3rd+ trade: Blocked — two chances is enough

Entry conditions (ALL must be true):
1. Price near recent high: within 8% of the 10-bar high
2. Price above VWAP: trading above institutional fair value
3. RSI sweet spot: between 55-90 (momentum without exhaustion)
4. Volume surge: current bar > 1.5x 50-bar volume SMA (3x for 2nd trade)
5. Price momentum: 10-bar ROC > 1.5% (3% for 2nd trade)
6. Bar closing strength: close in upper 60% of bar range

Stop: entry - (ATR × 2.0)
Target: 3:1 R/R
Exit: 2 consecutive bar closes below VWAP, RSI < 40, or price below 10-bar low
"""

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.bot.signals.base import Signal, SignalDirection, SignalGenerator
from src.data.indicators import atr, rsi, volume_sma


def calculate_vwap(bars: pd.DataFrame) -> pd.Series:
    """Calculate cumulative intraday VWAP, reset at each market open."""
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    tp_vol = typical_price * bars["volume"]

    # Use broker-provided VWAP if available and non-zero
    if "vwap" in bars.columns:
        broker_vwap = bars["vwap"]
        if (broker_vwap > 0).any():
            return broker_vwap

    # Fallback: calculate from OHLCV
    bars_et = bars.index.tz_convert("America/New_York")
    day_groups = bars_et.date
    cum_tp_vol = tp_vol.groupby(day_groups).cumsum()
    cum_vol = bars["volume"].groupby(day_groups).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, float("nan"))
    return vwap


class MomentumSurgeStrategy(SignalGenerator):
    """
    Momentum surge entry strategy on 5-min bars.

    Enters when a stock is actively surging with strong momentum indicators.
    Uses VWAP (Volume Weighted Average Price) as the momentum confirmation
    instead of MACD — VWAP responds instantly with no warmup lag.

    Trade-count-aware: applies different filters for 1st vs 2nd entries
    on the same symbol to allow valid second-wave surges while blocking
    faded chop re-entries.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.0,
        volume_period: int = 50,
        volume_multiplier: float = 1.5,
        roc_period: int = 10,
        roc_min: float = 0.015,
        rsi_min: float = 55.0,
        rsi_max: float = 90.0,
        hod_proximity: float = 0.08,
        daily_hod_max_drop: float = 0.05,
        risk_reward_target: float = 3.0,
        min_signal_strength: float = 0.5,
        max_trades_per_symbol: int = 2,
        # 2nd-trade thresholds (stricter momentum required)
        second_trade_volume_multiplier: float = 3.0,
        second_trade_roc_min: float = 0.03,
        # Accept but ignore legacy MACD params for backward compat
        macd_fast: int = 8,
        macd_slow: int = 21,
        macd_signal: int = 5,
    ):
        super().__init__(name="momentum_surge")
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.volume_period = volume_period
        self.volume_multiplier = volume_multiplier
        self.roc_period = roc_period
        self.roc_min = roc_min
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.hod_proximity = hod_proximity
        self.daily_hod_max_drop = daily_hod_max_drop
        self.risk_reward_target = risk_reward_target
        self.min_signal_strength = min_signal_strength
        self.max_trades_per_symbol = max_trades_per_symbol
        self.second_trade_volume_multiplier = second_trade_volume_multiplier
        self.second_trade_roc_min = second_trade_roc_min

        self.min_periods = max(volume_period, atr_period, roc_period) + 10

    def generate(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_price: Optional[float] = None,
        has_catalyst: bool = False,
        symbol_trade_count: int = 0,
    ) -> Optional[Signal]:
        """Generate a momentum surge entry signal.

        Args:
            symbol: Stock ticker
            bars: OHLCV DataFrame
            current_price: Optional live price override
            has_catalyst: Whether stock has news catalyst
            symbol_trade_count: How many completed trades on this symbol today
        """
        # 3rd+ trade on same symbol: blocked
        if symbol_trade_count >= self.max_trades_per_symbol:
            logger.debug(
                f"[SURGE] {symbol}: blocked — {symbol_trade_count} trades "
                f"already today (max {self.max_trades_per_symbol})"
            )
            return None

        is_second_trade = symbol_trade_count >= 1

        if not self.validate_bars(bars, self.min_periods):
            return None

        bars = self.normalize_bars(bars)

        close = bars["close"]
        high = bars["high"]
        low = bars["low"]
        volume = bars["volume"]

        current = current_price if current_price else float(close.iloc[-1])

        # ── Indicator calculations ──────────────────────────────────────
        vwap_values = calculate_vwap(bars)
        rsi_values = rsi(close, self.rsi_period)
        atr_values = atr(high, low, close, self.atr_period)
        avg_volume = volume_sma(volume, self.volume_period)

        # Rate of change (momentum)
        roc = close.pct_change(self.roc_period)

        # Current values
        cur_vwap = float(vwap_values.iloc[-1])
        cur_rsi = float(rsi_values.iloc[-1])
        cur_atr = float(atr_values.iloc[-1])
        cur_volume = float(volume.iloc[-1])
        cur_avg_volume = float(avg_volume.iloc[-1])
        cur_roc = float(roc.iloc[-1])

        # 10-bar high (recent trend high — avoids anchoring to distant spikes)
        bar_high_10 = float(high.iloc[-10:].max())

        # Daily HOD (true intraday high)
        bars_et = bars.index.tz_convert("America/New_York")
        today = bars_et[-1].date()
        today_mask = bars_et.date == today
        daily_hod = float(high[today_mask].max()) if today_mask.any() else bar_high_10
        drop_from_hod = (daily_hod - current) / daily_hod if daily_hod > 0 else 0

        # ── Entry conditions ────────────────────────────────────────────

        # 1. Price near recent high (within hod_proximity of 10-bar high)
        if bar_high_10 > 0 and (bar_high_10 - current) / bar_high_10 > self.hod_proximity:
            logger.debug(
                f"[SURGE] {symbol}: price ${current:.2f} too far from "
                f"10-bar high ${bar_high_10:.2f} "
                f"({(bar_high_10 - current) / bar_high_10:.1%} > {self.hod_proximity:.0%})"
            )
            return None

        # 2. Daily HOD proximity — trade-count-aware
        if not is_second_trade:
            # 1st trade: must be near daily HOD
            if daily_hod > 0 and drop_from_hod > self.daily_hod_max_drop:
                logger.debug(
                    f"[SURGE] {symbol}: price ${current:.2f} faded from "
                    f"daily HOD ${daily_hod:.2f} "
                    f"({drop_from_hod:.1%} drop > {self.daily_hod_max_drop:.0%} max)"
                )
                return None
        # 2nd trade: no daily HOD filter — allow second-wave entries
        # but requires stronger momentum (checked below in vol/ROC)

        # 3. Price above VWAP (trading above institutional fair value)
        if np.isnan(cur_vwap) or cur_vwap <= 0:
            logger.debug(f"[SURGE] {symbol}: VWAP unavailable")
            return None
        if current <= cur_vwap:
            logger.debug(
                f"[SURGE] {symbol}: price ${current:.2f} below "
                f"VWAP ${cur_vwap:.2f}"
            )
            return None

        price_vs_vwap = (current - cur_vwap) / cur_vwap

        # 4. RSI in sweet spot
        if cur_rsi < self.rsi_min or cur_rsi > self.rsi_max:
            logger.debug(
                f"[SURGE] {symbol}: RSI {cur_rsi:.1f} outside "
                f"{self.rsi_min}-{self.rsi_max} range"
            )
            return None

        # 5. Volume surge — stricter for 2nd trade
        volume_ratio = cur_volume / cur_avg_volume if cur_avg_volume > 0 else 0
        required_vol = (self.second_trade_volume_multiplier if is_second_trade
                        else self.volume_multiplier)
        if volume_ratio < required_vol:
            logger.debug(
                f"[SURGE] {symbol}: volume ratio {volume_ratio:.1f}x "
                f"< {required_vol}x required"
                f"{' (2nd trade)' if is_second_trade else ''}"
            )
            return None

        # 6. Price momentum (ROC) — stricter for 2nd trade
        required_roc = (self.second_trade_roc_min if is_second_trade
                        else self.roc_min)
        if cur_roc < required_roc:
            logger.debug(
                f"[SURGE] {symbol}: ROC {cur_roc:.1%} < {required_roc:.0%} min"
                f"{' (2nd trade)' if is_second_trade else ''}"
            )
            return None

        # 7. Bar closing strength — close in upper 60% of bar range
        cur_high = float(high.iloc[-1])
        cur_low = float(low.iloc[-1])
        cur_close = float(close.iloc[-1])
        bar_range = cur_high - cur_low
        if bar_range > 0:
            close_position = (cur_close - cur_low) / bar_range
            if close_position < 0.40:
                logger.debug(
                    f"[SURGE] {symbol}: bar close weak "
                    f"({close_position:.0%} of range, need >40%)"
                )
                return None

        # ── All conditions met — calculate signal ───────────────────────
        trade_label = f"2nd-trade " if is_second_trade else ""
        logger.info(
            f"[SURGE] {symbol}: {trade_label}ENTRY signal @ ${current:.2f} | "
            f"VWAP=${cur_vwap:.2f} (+{price_vs_vwap:.1%}) | "
            f"RSI={cur_rsi:.1f} | vol={volume_ratio:.1f}x | ROC={cur_roc:.1%}"
        )

        # Stop and target
        stop_price = current - (cur_atr * self.atr_stop_multiplier)
        risk = current - stop_price
        target_price = current + (risk * self.risk_reward_target)

        # Signal strength
        strength = self._calculate_strength(
            cur_rsi, volume_ratio, price_vs_vwap, has_catalyst
        )

        if strength < self.min_signal_strength:
            return None

        return Signal(
            symbol=symbol,
            direction=SignalDirection.LONG,
            strength=strength,
            entry_price=current,
            stop_price=stop_price,
            target_price=target_price,
            timeframe=self._detect_timeframe(bars),
            strategy=self.name,
            timestamp=datetime.now(),
            metadata={
                "system": "momentum_surge",
                "vwap": round(cur_vwap, 4),
                "price_vs_vwap": round(price_vs_vwap * 100, 2),
                "rsi": round(cur_rsi, 1),
                "volume_ratio": round(volume_ratio, 1),
                "roc_10": round(cur_roc * 100, 2),
                "atr": round(cur_atr, 4),
                "bar_high_10": round(bar_high_10, 2),
                "daily_hod": round(daily_hod, 2),
                "drop_from_hod": round(drop_from_hod * 100, 1),
                "symbol_trade_count": symbol_trade_count,
            },
        )

    def _calculate_strength(
        self,
        cur_rsi: float,
        volume_ratio: float,
        price_vs_vwap: float,
        has_catalyst: bool,
    ) -> float:
        """Calculate signal strength (0.0 - 1.0)."""
        strength = 0.50  # Base

        # RSI in ideal momentum zone (60-75)
        if 60 <= cur_rsi <= 75:
            strength += 0.10

        # Strong volume (>5x average — 1.5x is already the minimum entry)
        if volume_ratio > 5.0:
            strength += 0.10

        # Strong VWAP deviation (price >2% above VWAP)
        if price_vs_vwap > 0.02:
            strength += 0.10

        # News catalyst
        if has_catalyst:
            strength += 0.05

        return min(1.0, strength)

    def should_exit(
        self,
        symbol: str,
        bars: pd.DataFrame,
        entry_price: float,
        direction: SignalDirection,
        current_price: Optional[float] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a surge position should exit.

        Exit when:
        - 2 consecutive bar closes below VWAP (momentum fading)
        - RSI drops below 40 (momentum lost)
        - Price below 10-bar low (trend broken)
        """
        if not self.validate_bars(bars, self.min_periods):
            return False, None

        bars = self.normalize_bars(bars)

        close = bars["close"]
        low = bars["low"]

        current = current_price if current_price else float(close.iloc[-1])

        # VWAP exit — require 2 consecutive bar closes below VWAP
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

        # RSI collapse
        rsi_values = rsi(close, self.rsi_period)
        cur_rsi = float(rsi_values.iloc[-1])

        if cur_rsi < 40:
            return True, f"RSI collapsed to {cur_rsi:.1f}"

        # Price below 10-bar low (Donchian exit)
        ten_bar_low = float(low.iloc[-10:].min())
        if current < ten_bar_low:
            return True, f"Price ${current:.2f} below 10-bar low ${ten_bar_low:.2f}"

        return False, None

    def _detect_timeframe(self, bars: pd.DataFrame) -> str:
        """Detect timeframe from bar index."""
        if len(bars) < 2:
            return "unknown"
        try:
            delta = bars.index[-1] - bars.index[-2]
            minutes = delta.total_seconds() / 60
            if minutes <= 1:
                return "1Min"
            elif minutes <= 5:
                return "5Min"
            elif minutes <= 15:
                return "15Min"
            elif minutes <= 60:
                return "1Hour"
            elif minutes <= 1440:
                return "1Day"
            else:
                return "1Week"
        except Exception:
            return "5Min"
