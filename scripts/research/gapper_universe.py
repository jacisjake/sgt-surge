"""Reconstruct the historical gapper universe from daily OHLCV.

A symbol qualifies on day D if its open gaps up >= GAP_MIN over the prior close,
trades >= DOLLAR_VOL_MIN, and opens inside the price band. rank_day returns the
top-N qualifiers by gap%, mirroring the live top-5 selector. reconstruct() drives
this across a date range via SchwabClient.get_history daily bars.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

DEFAULT_PARAMS = {
    "gap_min": 0.20,
    "dollar_vol_min": 3_000_000.0,
    "price_min": 1.0,
    "price_max": 20.0,
    "top_n": 5,
}


def _gap_pct(row: dict) -> float:
    return row["open"] / row["prev_close"] - 1.0 if row["prev_close"] else 0.0


def qualifies(row: dict, params: dict) -> bool:
    if row["prev_close"] <= 0:
        return False
    if _gap_pct(row) < params["gap_min"]:
        return False
    if not (params["price_min"] <= row["open"] <= params["price_max"]):
        return False
    if row["close"] * row["volume"] < params["dollar_vol_min"]:
        return False
    return True


def rank_day(rows: dict, params: dict) -> list[str]:
    qualified = [(s, _gap_pct(r)) for s, r in rows.items() if qualifies(r, params)]
    qualified.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in qualified[: params["top_n"]]]


def reconstruct(client, symbols, start: date, end: date, params=None) -> dict:
    """Return {iso_date: [symbols]} of reconstructed gappers per trading day.

    Fetches daily bars per symbol once over [start-5d, end], then for each date
    builds the per-symbol {prev_close, open, close, volume} rows and ranks them.
    """
    params = params or DEFAULT_PARAMS
    fetch_start = start - timedelta(days=5)
    daily = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, end)
        if not df.empty:
            daily[sym] = df

    by_date: dict[str, dict] = {}
    for sym, df in daily.items():
        closes = df["close"].tolist()
        opens = df["open"].tolist()
        vols = df["volume"].tolist()
        dates = [ts.date() for ts in df.index]
        for i in range(1, len(df)):
            d = dates[i]
            if d < start or d > end:
                continue
            by_date.setdefault(d.isoformat(), {})[sym] = {
                "prev_close": closes[i - 1],
                "open": opens[i],
                "close": closes[i],
                "volume": vols[i],
            }

    return {d: rank_day(rows, params) for d, rows in sorted(by_date.items())}


def compute_levels(daily_df, day: date, swing_lookback: int = 10) -> Optional[dict]:
    """Compute prior-day and swing levels relative to *day*.

    daily_df: DataFrame with a tz-aware DatetimeIndex and high/low columns.
    day: the trading date we want levels *for* (not included in the calculation).
    Returns {"prev_high", "prev_low", "swing_high", "swing_low"} or None when
    there is no prior trading day in daily_df.
    """
    dates = [ts.date() for ts in daily_df.index]
    # find index of the last row whose date is strictly before day
    prior_idx = None
    for i, d in enumerate(dates):
        if d < day:
            prior_idx = i
    if prior_idx is None:
        return None

    prev_row = daily_df.iloc[prior_idx]
    prev_high = float(prev_row["high"])
    prev_low = float(prev_row["low"])

    # swing window: up to swing_lookback rows BEFORE prior_idx
    swing_start = max(0, prior_idx - swing_lookback)
    swing_df = daily_df.iloc[swing_start:prior_idx]
    if swing_df.empty:
        swing_high = prev_high
        swing_low = prev_low
    else:
        swing_high = float(swing_df["high"].max())
        swing_low = float(swing_df["low"].min())

    return {
        "prev_high": prev_high,
        "prev_low": prev_low,
        "swing_high": swing_high,
        "swing_low": swing_low,
    }
