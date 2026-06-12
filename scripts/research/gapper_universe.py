"""Reconstruct the historical gapper universe from daily OHLCV.

A symbol qualifies on day D if its open gaps up >= GAP_MIN over the prior close,
trades >= DOLLAR_VOL_MIN, and opens inside the price band. rank_day returns the
top-N qualifiers by gap%, mirroring the live top-5 selector. reconstruct() drives
this across a date range via SchwabClient.get_history daily bars.
"""
from __future__ import annotations

from datetime import date, timedelta

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
