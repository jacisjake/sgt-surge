"""Per-day intraday indicator context for the setups."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional

import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
SESSION_OPEN = time(9, 30)
OR_END = time(9, 45)
SESSION_CLOSE = time(16, 0)


@dataclass
class Ctx:
    bars: pd.DataFrame          # session bars + cols: et_time, vwap, ema9, atr
    or_high: float
    or_low: float
    or_volume: float
    pm_high: Optional[float]
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def build_context(day_bars: pd.DataFrame, levels=None) -> Ctx:
    et = day_bars.index.tz_convert(ET)
    et_time = pd.Series([t.time() for t in et], index=day_bars.index)

    pm_mask = et_time < SESSION_OPEN
    pm_high = float(day_bars.loc[pm_mask, "high"].max()) if pm_mask.any() else None

    sess = day_bars.loc[(et_time >= SESSION_OPEN) & (et_time <= SESSION_CLOSE)].copy()
    sess["et_time"] = et_time[sess.index].values

    or_mask = (sess["et_time"] >= SESSION_OPEN) & (sess["et_time"] < OR_END)
    or_bars = sess.loc[or_mask]
    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_volume = float(or_bars["volume"].sum())

    typical = (sess["high"] + sess["low"] + sess["close"]) / 3
    sess["vwap"] = (typical * sess["volume"]).cumsum() / sess["volume"].cumsum()
    sess["ema9"] = sess["close"].ewm(span=9, adjust=False).mean()
    sess["atr"] = _atr(sess)

    lvl = levels or {}
    return Ctx(bars=sess, or_high=or_high, or_low=or_low,
               or_volume=or_volume, pm_high=pm_high,
               prev_high=lvl.get("prev_high"),
               prev_low=lvl.get("prev_low"),
               swing_high=lvl.get("swing_high"),
               swing_low=lvl.get("swing_low"))
