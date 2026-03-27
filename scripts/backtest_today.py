#!/usr/bin/env python3
"""
Quick backtest of today's scanner symbols with current surge config.

Fetches 5-min bars from yfinance and runs the surge strategy to see
what signals would have fired and how trades would have played out.
"""

import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)

import yfinance as yf
import pandas as pd
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="DEBUG", format="{message}")

from src.bot.signals.momentum_surge import MomentumSurgeStrategy
from src.bot.signals.base import SignalDirection


def fetch_bars(symbol: str) -> pd.DataFrame:
    """Fetch today's 5-min bars."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1d", interval="5m")
    if df.empty:
        return df
    df.columns = [c.lower() for c in df.columns]
    if "vwap" not in df.columns:
        df["vwap"] = 0.0
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def simulate_trade(bars: pd.DataFrame, entry_idx: int, entry_price: float,
                   stop_price: float, strategy) -> dict:
    """Simulate from entry to exit."""
    max_price = entry_price
    exit_price = None
    exit_reason = None
    exit_idx = None

    for i in range(entry_idx + 1, len(bars)):
        cur_high = float(bars["high"].iloc[i])
        cur_low = float(bars["low"].iloc[i])
        cur_price = float(bars["close"].iloc[i])
        max_price = max(max_price, cur_high)

        if cur_low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
            exit_idx = i
            break

        if i >= 40:
            sub_bars = bars.iloc[max(0, i - 99):i + 1]
            if len(sub_bars) >= 40:
                try:
                    should_exit, reason = strategy.should_exit(
                        symbol="TEST", bars=sub_bars,
                        entry_price=entry_price,
                        direction=SignalDirection.LONG,
                        current_price=cur_price,
                    )
                    if should_exit:
                        exit_price = cur_price
                        exit_reason = reason
                        exit_idx = i
                        break
                except Exception:
                    pass

    if exit_price is None:
        exit_price = float(bars["close"].iloc[-1])
        exit_reason = "open (still holding)"
        exit_idx = len(bars) - 1

    risk = entry_price - stop_price
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    r_multiple = (exit_price - entry_price) / risk if risk != 0 else 0

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "stop_price": stop_price,
        "pnl_pct": pnl_pct,
        "r_multiple": r_multiple,
        "max_price": max_price,
        "max_r": (max_price - entry_price) / risk if risk != 0 else 0,
        "exit_reason": exit_reason,
        "bars_held": exit_idx - entry_idx,
    }


def main():
    # Today's scanner symbols from bot logs
    symbols = [
        "CVV", "ANNA", "QNTM", "MRLN", "UGRO", "TRON", "IONR",
        "NUCL", "GLWG", "YDES", "AAPG", "EUDA", "VWAV", "FCHL",
    ]

    # Current production config (with HOD proximity removed)
    strategy = MomentumSurgeStrategy(
        atr_period=14, atr_stop_multiplier=1.0,
        roc_min=0.03,
        rsi_min=50.0, rsi_max=80.0,
        risk_reward_target=10.0, min_signal_strength=0.6,
    )

    print("=" * 80)
    print("  TODAY'S BACKTEST — New config (no HOD proximity, no relVol gate)")
    print("=" * 80)

    bars_cache = {}
    for symbol in symbols:
        print(f"  Fetching {symbol}...", end=" ", flush=True)
        bars = fetch_bars(symbol)
        if bars.empty or len(bars) < 40:
            print(f"skip ({len(bars)} bars)")
            continue
        bars_cache[symbol] = bars
        open_price = float(bars["open"].iloc[0])
        last_price = float(bars["close"].iloc[-1])
        high = float(bars["high"].max())
        vol = int(bars["volume"].sum())
        change = (last_price - open_price) / open_price * 100 if open_price > 0 else 0
        dollar_vol = last_price * vol
        print(f"{len(bars)} bars | ${open_price:.2f}→${last_price:.2f} "
              f"(+{change:.1f}%) HOD=${high:.2f} $vol=${dollar_vol/1e6:.1f}M")

    print(f"\n  {len(bars_cache)} symbols loaded\n")

    # Run strategy on each symbol
    all_trades = []
    all_signals = []
    for symbol, bars in bars_cache.items():
        seen_today = False
        for i in range(40, len(bars) - 1):
            if seen_today:
                break
            window = bars.iloc[max(0, i - 99):i + 1]
            cur_price = float(bars["close"].iloc[i])
            signal = strategy.generate(symbol, window, cur_price)
            if signal is None:
                continue

            seen_today = True
            entry_time = bars.index[i]
            if hasattr(entry_time, "tz_convert"):
                entry_time_et = entry_time.tz_convert("America/New_York")
            else:
                entry_time_et = entry_time

            all_signals.append({
                "symbol": symbol,
                "time": entry_time_et,
                "price": signal.entry_price,
                "stop": signal.stop_price,
                "strength": signal.strength,
            })

            result = simulate_trade(bars, i, signal.entry_price, signal.stop_price, strategy)
            result["symbol"] = symbol
            result["entry_time"] = entry_time_et
            result["signal_strength"] = signal.strength
            all_trades.append(result)

    # Print signals
    print(f"  SIGNALS FIRED: {len(all_signals)}")
    print(f"  {'Symbol':<8} {'Time':>12} {'Price':>8} {'Stop':>8} {'Str':>5}")
    print(f"  {'─'*8} {'─'*12} {'─'*8} {'─'*8} {'─'*5}")
    for s in sorted(all_signals, key=lambda x: x["time"]):
        t = s["time"].strftime("%H:%M") if hasattr(s["time"], "strftime") else str(s["time"])
        print(f"  {s['symbol']:<8} {t:>12} ${s['price']:>7.2f} ${s['stop']:>7.2f} {s['strength']:>5.2f}")

    # Print trades
    if all_trades:
        print(f"\n  TRADE RESULTS:")
        print(f"  {'Symbol':<8} {'Entry':>8} {'Exit':>8} {'P&L':>8} "
              f"{'R':>6} {'MaxR':>6} {'Bars':>5} {'Exit Reason':<30}")
        print(f"  {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*5} {'─'*30}")

        total_r = 0
        wins = 0
        for t in sorted(all_trades, key=lambda x: x["entry_time"]):
            pnl_str = f"{t['pnl_pct']:+.1f}%"
            total_r += t["r_multiple"]
            if t["pnl_pct"] > 0:
                wins += 1
            print(f"  {t['symbol']:<8} ${t['entry_price']:>7.2f} "
                  f"${t['exit_price']:>7.2f} {pnl_str:>8} "
                  f"{t['r_multiple']:>+5.1f}R {t['max_r']:>5.1f}R "
                  f"{t['bars_held']:>5} {t['exit_reason']:<30}")

        n = len(all_trades)
        print(f"\n  Summary: {n} trades | {wins}/{n} wins ({wins/n*100:.0f}%) | "
              f"Total: {total_r:+.1f}R")
    else:
        print("\n  No trades generated.")

    # Show symbols that DIDN'T fire
    no_signal = [s for s in bars_cache if s not in [t["symbol"] for t in all_trades]]
    if no_signal:
        print(f"\n  No signal: {', '.join(no_signal)}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
