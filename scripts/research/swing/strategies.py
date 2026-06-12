"""Pure strategy functions over daily OHLCV DataFrames.

Each function accepts a DataFrame with columns open/high/low/close/volume and
a DatetimeIndex, and returns list[float] of per-trade fractional returns after
slippage.  Slippage is applied as 2 * slip_bps / 10_000 (entry + exit crossing).
"""
from __future__ import annotations

import datetime

import pandas as pd


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
                exit_price = stop_level
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
                exit_price = stop_level
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
                exit_price = stop_level
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
