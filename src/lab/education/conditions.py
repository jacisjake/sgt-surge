"""Simple, explainable market-condition sensors (SPY-centric).

Tags are teachable — not a black-box regime model. All signals are causal
given a daily OHLCV frame ending on the evaluation date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.lab.protocol import bar_index_for_date
from src.lab.strategies._common import build_risk_on


# Canonical tags used by playbook.yaml
TAG_RISK_ON = "risk_on"
TAG_RISK_OFF = "risk_off"
TAG_CHOP = "chop"
TAG_ELEVATED_VOL = "elevated_vol"


@dataclass
class MarketCondition:
    as_of: date
    tags: list[str]
    confidence: str  # low | medium | high
    evidence: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


def _atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def classify_spy(
    spy_df: pd.DataFrame,
    as_of: Optional[date] = None,
    *,
    sma_long: int = 200,
    sma_short: int = 20,
    atr_period: int = 20,
    vol_lookback: int = 120,
    chop_band_pct: float = 0.015,
    chop_range_ratio: float = 0.35,
) -> MarketCondition:
    """Classify market conditions from SPY daily bars.

    Rules (v1, intentionally simple):
      - risk_on  if close > SMA(sma_long); else risk_off
      - elevated_vol if ATR%/close is above 75th pct of trailing vol_lookback
      - chop if near SMA(sma_short) (|dev| < chop_band_pct) AND recent 10d range
        is small vs 60d range (ratio < chop_range_ratio)
    """
    if spy_df is None or spy_df.empty:
        d = as_of or date.today()
        return MarketCondition(
            as_of=d,
            tags=[TAG_RISK_OFF],
            confidence="low",
            evidence={"error": "no SPY bars"},
            summary="No SPY data — treat as risk-off / unknown (do not force new risk).",
        )

    if as_of is None:
        as_of = spy_df.index[-1].date()

    i = bar_index_for_date(spy_df, as_of)
    if i is None:
        # use last bar on or before as_of
        eligible = [j for j, ts in enumerate(spy_df.index) if ts.date() <= as_of]
        if not eligible:
            return MarketCondition(
                as_of=as_of,
                tags=[TAG_RISK_OFF],
                confidence="low",
                evidence={"error": "as_of before SPY history"},
                summary="SPY history does not reach as_of — unknown regime.",
            )
        i = eligible[-1]
        as_of = spy_df.index[i].date()

    # Slice to as_of (causal)
    df = spy_df.iloc[: i + 1].copy()
    close = float(df["close"].iloc[-1])
    sma_l = df["close"].rolling(sma_long).mean().iloc[-1]
    sma_s = df["close"].rolling(sma_short).mean().iloc[-1]
    atr = _atr(df, atr_period)
    atr_now = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None

    risk_map = build_risk_on(df, sma_period=sma_long)
    risk_on = bool(risk_map.get(as_of, False))

    tags: list[str] = [TAG_RISK_ON if risk_on else TAG_RISK_OFF]
    evidence: dict[str, Any] = {
        "symbol": "SPY",
        "close": close,
        "sma_long": None if pd.isna(sma_l) else float(sma_l),
        "sma_short": None if pd.isna(sma_s) else float(sma_s),
        "sma_long_period": sma_long,
        "sma_short_period": sma_short,
        "risk_on": risk_on,
    }

    # Elevated vol
    if atr_now is not None and close > 0 and len(df) >= max(atr_period + 5, 40):
        atr_pct = atr_now / close
        atr_pct_series = (atr / df["close"]).dropna()
        window = atr_pct_series.iloc[-vol_lookback:] if len(atr_pct_series) else atr_pct_series
        p75 = float(np.nanpercentile(window.to_numpy(), 75)) if len(window) else None
        evidence["atr"] = atr_now
        evidence["atr_pct"] = atr_pct
        evidence["atr_pct_p75"] = p75
        if p75 is not None and atr_pct >= p75:
            tags.append(TAG_ELEVATED_VOL)

    # Chop: flat vs short SMA + compressed range
    if not pd.isna(sma_s) and close > 0 and len(df) >= 60:
        dev = abs(close / float(sma_s) - 1.0)
        r10 = float(df["high"].iloc[-10:].max() - df["low"].iloc[-10:].min())
        r60 = float(df["high"].iloc[-60:].max() - df["low"].iloc[-60:].min())
        ratio = (r10 / r60) if r60 > 0 else 1.0
        evidence["sma_short_dev_pct"] = dev
        evidence["range_10d"] = r10
        evidence["range_60d"] = r60
        evidence["range_ratio_10_60"] = ratio
        if dev <= chop_band_pct and ratio <= chop_range_ratio:
            tags.append(TAG_CHOP)

    # Confidence
    conf = "medium"
    if pd.isna(sma_l) or len(df) < sma_long:
        conf = "low"
    elif TAG_CHOP in tags and TAG_ELEVATED_VOL in tags:
        conf = "medium"  # mixed signals
    elif len(df) >= sma_long + 20:
        conf = "high"

    summary = _summary_line(tags, evidence)
    return MarketCondition(
        as_of=as_of,
        tags=tags,
        confidence=conf,
        evidence=evidence,
        summary=summary,
    )


def _summary_line(tags: list[str], evidence: dict[str, Any]) -> str:
    parts = []
    if TAG_RISK_ON in tags:
        parts.append("Risk-on (SPY above long SMA)")
    else:
        parts.append("Risk-off (SPY at/below long SMA)")
    if TAG_CHOP in tags:
        parts.append("chop / range-bound short-term")
    if TAG_ELEVATED_VOL in tags:
        parts.append("elevated volatility")
    base = "; ".join(parts) + "."
    c = evidence.get("close")
    s = evidence.get("sma_long")
    if c is not None and s is not None:
        base += f" SPY {c:.2f} vs SMA{evidence.get('sma_long_period', 200)} {s:.2f}."
    return base
