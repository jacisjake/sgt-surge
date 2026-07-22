"""Pure strategy functions over daily OHLCV DataFrames.

Each function accepts a DataFrame with columns open/high/low/close/volume and
a DatetimeIndex, and returns list[float] of per-trade fractional returns after
slippage.  Slippage is applied as 2 * slip_bps / 10_000 (entry + exit crossing).

Shared lab helpers (stop_fill_price, build_risk_on, is_fresh_breakout) live in
``src.lab.strategies._common`` and are re-exported here for research scripts.
"""
from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from src.lab.strategies._common import build_risk_on, is_fresh_breakout, stop_fill_price

__all__ = [
    "stop_fill_price",
    "build_risk_on",
    "is_fresh_breakout",
    "overnight_drift",
    "short_term_reversal",
    "short_term_reversal_trades",
    "trend_pullback_trades",
    "index_rsi2_trades",
    "turn_of_month_trades",
    "breakout_52w_trades",
]


def _rsi(close, period):
    import pandas as pd
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def overnight_drift(df: pd.DataFrame, slip_bps: float = 15.0) -> list[float]:
    """Unconditional overnight hold: buy each day's close, sell next day's open.

    Trade return = open[i+1]/close[i] - 1 - 2*slip.  One trade per night.
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    opens = df["open"].to_numpy()
    result: list[float] = []
    for i in range(len(df) - 1):
        ret = opens[i + 1] / closes[i] - 1.0 - slip
        result.append(ret)
    return result


def short_term_reversal(
    df: pd.DataFrame,
    down_days: int = 3,
    hold: int = 5,
    stop_pct: float = 0.05,
    target_pct: float = 0.10,
    ma: int = 200,
    slip_bps: float = 15.0,
) -> list[float]:
    """Buy oversold dips in an uptrend, exit on stop/target/time.

    Entry on day i when:
      - close[i] > SMA(close, ma)[i]  (uptrend filter)
      - the last `down_days` closes are strictly decreasing:
        close[i] < close[i-1] < ... < close[i-down_days]

    Enter at close[i].  Over days i+1 .. i+hold:
      - if low[j] <= entry*(1-stop_pct): exit at entry*(1-stop_pct)  (checked first)
      - elif high[j] >= entry*(1+target_pct): exit at entry*(1+target_pct)
      - after `hold` days with neither: exit at close[i+hold].

    Trade return = exit/entry - 1 - 2*slip.  Trades may overlap.
    Skip entries where i < ma (not enough SMA history) or i+1 >= len(df).
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    sma = df["close"].rolling(ma).mean().to_numpy()

    result: list[float] = []
    n = len(df)

    for i in range(down_days, n - 1):
        # Need valid SMA (rolling(ma) is NaN for first ma-1 rows)
        if pd.isna(sma[i]):
            continue

        # Uptrend filter
        if closes[i] <= sma[i]:
            continue

        # Strictly decreasing closes: close[i] < close[i-1] < ... < close[i-down_days]
        decreasing = all(
            closes[i - k] < closes[i - k - 1] for k in range(down_days)
        )
        if not decreasing:
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)
        target_level = entry * (1.0 + target_pct)

        exit_price = None
        for j in range(i + 1, min(i + 1 + hold, n)):
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                break
            if highs[j] >= target_level:
                exit_price = target_level
                break
        if exit_price is None:
            # time exit: close at end of hold period (j = i+hold, but capped at n-1)
            exit_idx = min(i + hold, n - 1)
            exit_price = closes[exit_idx]

        ret = exit_price / entry - 1.0 - slip
        result.append(ret)

    return result


def trend_pullback_trades(
    df: pd.DataFrame,
    symbol: str,
    down_days: int = 3,
    ma_entry: int = 200,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> list[dict]:
    """Buy-the-dip-in-an-uptrend, hold the trend.

    Entry on day i when: close[i] > SMA(close, ma_entry)[i]  (uptrend filter)
      AND the last `down_days` closes strictly decreasing
      (close[i] < close[i-1] < ... < close[i-down_days]).
    Enter at close[i]. Hard stop at entry*(1-stop_pct). Over days j=i+1..end:
      - if low[j] <= stop: exit at stop (CHECK FIRST, conservative)
      - elif not NaN(SMA(close, ma_exit)[j]) and close[j] < SMA_exit[j]:
            exit at close[j]   (trend break)
    If neither ever triggers, exit at the last close.
    Returns one dict per trade: {"symbol", "entry_date" (date), "exit_date" (date),
      "return_pct" (after 2*slip), "stop_pct"}.  entry_date=df.index[i].date(),
      exit_date=df.index[exit_j].date().  Skip i where SMA_entry is NaN or i+1>=len.
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    sma_entry = df["close"].rolling(ma_entry).mean().to_numpy()
    sma_exit = df["close"].rolling(ma_exit).mean().to_numpy()
    index = df.index

    result: list[dict] = []
    n = len(df)

    for i in range(down_days, n - 1):
        # Need valid SMA_entry
        if pd.isna(sma_entry[i]):
            continue

        # Uptrend filter
        if closes[i] <= sma_entry[i]:
            continue

        # Strictly decreasing closes: close[i] < close[i-1] < ... < close[i-down_days]
        decreasing = all(
            closes[i - k] < closes[i - k - 1] for k in range(down_days)
        )
        if not decreasing:
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)

        exit_price = None
        exit_j = None

        for j in range(i + 1, n):
            # Check hard stop first (conservative)
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                exit_j = j
                break
            # Check trend break: close below SMA_exit (only when SMA_exit is valid)
            if not pd.isna(sma_exit[j]) and closes[j] < sma_exit[j]:
                exit_price = closes[j]
                exit_j = j
                break

        # Time exit: last bar if no stop or trend break
        if exit_price is None:
            exit_j = n - 1
            exit_price = closes[exit_j]

        ret = exit_price / entry - 1.0 - slip
        result.append({
            "symbol": symbol,
            "entry_date": index[i].date(),
            "exit_date": index[exit_j].date(),
            "return_pct": ret,
            "stop_pct": stop_pct,
        })

    return result


def short_term_reversal_trades(
    df: pd.DataFrame,
    symbol: str,
    down_days: int = 3,
    hold: int = 5,
    stop_pct: float = 0.05,
    target_pct: float = 0.10,
    ma: int = 200,
    slip_bps: float = 15.0,
) -> list[dict]:
    """Dated-trade variant of short_term_reversal.

    Identical entry/exit logic; returns one dict per trade instead of bare floats.

    Returns
    -------
    list of dicts, each containing:
      "symbol"     : the ticker string passed in
      "entry_date" : datetime.date — df.index[i].date() at entry bar
      "exit_date"  : datetime.date — df.index[exit_j].date() at exit bar
      "return_pct" : float — after slippage (same value as short_term_reversal)
      "stop_pct"   : float — the stop_pct argument
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    sma = df["close"].rolling(ma).mean().to_numpy()
    index = df.index

    result: list[dict] = []
    n = len(df)

    for i in range(down_days, n - 1):
        if pd.isna(sma[i]):
            continue

        if closes[i] <= sma[i]:
            continue

        decreasing = all(
            closes[i - k] < closes[i - k - 1] for k in range(down_days)
        )
        if not decreasing:
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)
        target_level = entry * (1.0 + target_pct)

        exit_price = None
        exit_j = None
        for j in range(i + 1, min(i + 1 + hold, n)):
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                exit_j = j
                break
            if highs[j] >= target_level:
                exit_price = target_level
                exit_j = j
                break
        if exit_price is None:
            exit_j = min(i + hold, n - 1)
            exit_price = closes[exit_j]

        ret = exit_price / entry - 1.0 - slip
        result.append({
            "symbol": symbol,
            "entry_date": index[i].date(),
            "exit_date": index[exit_j].date(),
            "return_pct": ret,
            "stop_pct": stop_pct,
        })

    return result


def index_rsi2_trades(
    df: pd.DataFrame,
    symbol: str,
    ma: int = 200,
    rsi_buy: float = 10.0,
    rsi_sell: float = 70.0,
    max_hold: int = 10,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> list[dict]:
    """Mean-reversion via Connors RSI-2.

    Entry on day i: close[i] > SMA(close, ma)[i] AND rsi2[i] < rsi_buy.
    Enter at close[i].

    Exit on first day j > i where:
      1. low[j] <= entry*(1-stop_pct) → exit at stop_level (checked FIRST)
      2. rsi2[j] > rsi_sell → exit at close[j]
      3. j - i >= max_hold → time exit at close[j]
    If none triggers before the end of data, exit at the last close.

    Skip i where sma[i] or rsi2[i] is NaN, or i+1 >= len(df).

    Returns one dict per trade: {symbol, entry_date, exit_date, return_pct, stop_pct}.
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    sma = df["close"].rolling(ma).mean().to_numpy()
    rsi2 = _rsi(df["close"], 2).to_numpy()
    index = df.index

    result: list[dict] = []
    n = len(df)

    for i in range(1, n - 1):
        if pd.isna(sma[i]) or pd.isna(rsi2[i]):
            continue
        # Entry conditions
        if closes[i] <= sma[i]:
            continue
        if rsi2[i] >= rsi_buy:
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)

        exit_price = None
        exit_j = None

        for j in range(i + 1, n):
            # 1. Hard stop (checked first)
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                exit_j = j
                break
            # 2. RSI2 recovery exit
            if not pd.isna(rsi2[j]) and rsi2[j] > rsi_sell:
                exit_price = closes[j]
                exit_j = j
                break
            # 3. Time exit
            if j - i >= max_hold:
                exit_price = closes[j]
                exit_j = j
                break

        # Last-bar exit if nothing triggered
        if exit_price is None:
            exit_j = n - 1
            exit_price = closes[exit_j]

        ret = exit_price / entry - 1.0 - slip
        result.append({
            "symbol": symbol,
            "entry_date": index[i].date(),
            "exit_date": index[exit_j].date(),
            "return_pct": ret,
            "stop_pct": stop_pct,
        })

    return result


def turn_of_month_trades(
    df: pd.DataFrame,
    symbol: str,
    hold: int = 4,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> list[dict]:
    """Calendar seasonality: buy the last trading day of each month.

    A "last trading day of month" is index i where i+1 < n and
    df.index[i].month != df.index[i+1].month.  Enter at close[i].

    Exit at close[i+hold] (capped at last index), UNLESS a hard stop hits
    first: scan j=i+1..i+hold; if low[j] <= entry*(1-stop_pct) exit at stop.
    One trade per month-end.

    Returns one dict per trade: {symbol, entry_date, exit_date, return_pct, stop_pct}.
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    index = df.index

    result: list[dict] = []
    n = len(df)

    for i in range(n - 1):
        # Last trading day of month: next bar is a different month
        if index[i].month == index[i + 1].month:
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)

        exit_price = None
        exit_j = None

        end_j = min(i + hold, n - 1)
        for j in range(i + 1, end_j + 1):
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                exit_j = j
                break

        if exit_price is None:
            exit_j = end_j
            exit_price = closes[exit_j]

        ret = exit_price / entry - 1.0 - slip
        result.append({
            "symbol": symbol,
            "entry_date": index[i].date(),
            "exit_date": index[exit_j].date(),
            "return_pct": ret,
            "stop_pct": stop_pct,
        })

    return result


def breakout_52w_trades(
    df: pd.DataFrame,
    symbol: str,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
    risk_on: Optional[dict] = None,
) -> list[dict]:
    """Momentum breakout: buy the FIRST bar of a new 52-week (lookback-bar) high.

    A "fresh breakout" at day i (i >= lookback):
      close[i] >= max(high[i-lookback : i])   — current bar is at new high
      AND close[i-1] < max(high[i-1-lookback : i-1])  — prior bar was NOT

    Enter at close[i].

    Exit on first j > i where:
      1. low[j] <= entry*(1-stop_pct) → exit at stop_level (checked FIRST)
      2. close[j] < SMA(close, ma_exit)[j] → trend-break exit at close[j]
    If neither triggers, exit at last close.

    `risk_on` is the optional market-regime gate (see build_risk_on): a
    date -> bool map; entries on risk-off dates are skipped, and a date missing
    from the map is treated as risk-off. Pass None (default) to run ungated.
    Gating ENTRIES only — an open position must always be free to exit.

    Returns one dict per trade: {symbol, entry_date, exit_date, return_pct, stop_pct}.
    """
    slip = 2 * slip_bps / 10_000
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    sma_exit = df["close"].rolling(ma_exit).mean().to_numpy()
    index = df.index

    result: list[dict] = []
    n = len(df)

    for i in range(lookback, n - 1):
        # Current bar: close >= max of prior `lookback` highs
        window_cur_max = highs[i - lookback: i].max()
        if closes[i] < window_cur_max:
            continue

        # Prior bar: must NOT have been at a new high (freshness filter)
        prev_window_start = max(0, i - 1 - lookback)
        window_prev_max = highs[prev_window_start: i - 1].max()
        if closes[i - 1] >= window_prev_max:
            continue  # prior bar was also at a new high — not a fresh breakout

        # Market-regime gate: don't buy breakouts into a downtrending market.
        if risk_on is not None and not risk_on.get(index[i].date(), False):
            continue

        entry = closes[i]
        stop_level = entry * (1.0 - stop_pct)

        exit_price = None
        exit_j = None

        for j in range(i + 1, n):
            # 1. Hard stop (checked first)
            if lows[j] <= stop_level:
                exit_price = stop_fill_price(stop_level, opens[j])
                exit_j = j
                break
            # 2. Trend-break exit: close below SMA_exit
            if not pd.isna(sma_exit[j]) and closes[j] < sma_exit[j]:
                exit_price = closes[j]
                exit_j = j
                break

        # Last-bar exit if nothing triggered
        if exit_price is None:
            exit_j = n - 1
            exit_price = closes[exit_j]

        ret = exit_price / entry - 1.0 - slip
        result.append({
            "symbol": symbol,
            "entry_date": index[i].date(),
            "exit_date": index[exit_j].date(),
            "return_pct": ret,
            "stop_pct": stop_pct,
        })

    return result
