#!/usr/bin/env python3
"""
Diagnose why surge trades are failing on $1-$10 stocks.

Categorizes trades into:
1. Bad entries (MaxR < 0.5R — never went in our favor)
2. Premature exits (MaxR >= 2R but captured < 1R)
3. Clean wins (captured >= 1R)
4. Clean losses (MaxR < 1R, stopped out correctly)

Then analyzes exit reasons and timing.
"""

import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)

import yfinance as yf
import pandas as pd
import numpy as np
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

from src.bot.signals.momentum_surge import MomentumSurgeStrategy
from src.bot.signals.base import SignalDirection


def fetch_bars(symbol: str, period: str = "5d", interval: str = "5m") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)
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


def simulate_trade_detailed(bars: pd.DataFrame, entry_idx: int, entry_price: float,
                            stop_price: float, strategy) -> dict:
    """Simulate trade with detailed bar-by-bar tracking."""
    max_price = entry_price
    max_r = 0.0
    risk = entry_price - stop_price
    exit_price = None
    exit_reason = None
    exit_idx = None
    bars_to_max = 0
    bars_positive = 0

    for i in range(entry_idx + 1, len(bars)):
        cur_price = float(bars["close"].iloc[i])
        cur_high = float(bars["high"].iloc[i])
        cur_low = float(bars["low"].iloc[i])

        if cur_high > max_price:
            max_price = cur_high
            bars_to_max = i - entry_idx
        cur_r = (max_price - entry_price) / risk if risk != 0 else 0
        max_r = max(max_r, cur_r)

        if cur_price > entry_price:
            bars_positive += 1

        # Check stop loss
        if cur_low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
            exit_idx = i
            break

        # Check strategy exit
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
        exit_reason = "end_of_data"
        exit_idx = len(bars) - 1

    pnl_pct = (exit_price - entry_price) / entry_price * 100
    r_multiple = (exit_price - entry_price) / risk if risk != 0 else 0
    bars_held = exit_idx - entry_idx

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "stop_price": stop_price,
        "pnl_pct": pnl_pct,
        "r_multiple": r_multiple,
        "max_price": max_price,
        "max_r": max_r,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "bars_to_max": bars_to_max,
        "bars_positive": bars_positive,
        "left_on_table_r": max_r - r_multiple,
    }


def run_backtest(strategy, symbols, bars_cache, max_price=10.0):
    all_trades = []
    for symbol in symbols:
        bars = bars_cache.get(symbol)
        if bars is None or len(bars) < 50:
            continue

        daily_trade_counts = {}
        active_until = {}

        for i in range(40, len(bars) - 5):
            window = bars.iloc[max(0, i - 99):i + 1]
            cur_price = float(bars["close"].iloc[i])

            if cur_price < 2.50 or cur_price > max_price:
                continue

            bar_time = bars.index[i]
            if hasattr(bar_time, 'tz_convert'):
                day_str = str(bar_time.tz_convert("America/New_York").date())
            else:
                day_str = str(bar_time.date())

            key = (symbol, day_str)
            if key in active_until and i < active_until[key]:
                continue

            symbol_trade_count = daily_trade_counts.get(day_str, 0)
            if symbol_trade_count >= 2:
                continue

            signal = strategy.generate(symbol, window, cur_price,
                                       symbol_trade_count=symbol_trade_count)
            if signal is None:
                continue

            result = simulate_trade_detailed(bars, i, signal.entry_price,
                                             signal.stop_price, strategy)
            result["symbol"] = symbol
            result["entry_time"] = bars.index[i]
            result["signal_strength"] = signal.strength
            meta = signal.metadata or {}
            result["rsi"] = meta.get("rsi", 0)
            result["volume_ratio"] = meta.get("volume_ratio", 0)
            result["roc_10"] = meta.get("roc_10", 0)
            result["price_vs_vwap"] = meta.get("price_vs_vwap", 0)

            all_trades.append(result)
            active_until[key] = i + result["bars_held"]
            daily_trade_counts[day_str] = symbol_trade_count + 1

    return pd.DataFrame(all_trades) if all_trades else pd.DataFrame()


def main():
    symbols = [
        "ARTL", "VSA", "ONCO", "TURB", "AGX", "KOD", "ADMA", "RBNE", "ADV", "CRE",
        "AIFF", "EEIQ", "FCHL", "NDLS", "RVI", "PGEN", "BATL", "ARMG", "UGRO",
        "CAR", "NAVN", "RKLZ", "SMCZ",
        "BRAI", "RRGB", "MGN", "USGO", "SPWH", "SHMD",
        "SYNX", "LUNG", "ANTX", "DTCK", "PAVS", "POLA", "WOOF",
        "SPRC", "SHIM", "SVCO",
        "AIRS", "CTMX", "DSGR", "HBIO", "HCWB", "JZ", "LNZA",
        "NSA", "OPAL", "ULY",
    ]

    print("Fetching data...", flush=True)
    bars_cache = {}
    for symbol in symbols:
        bars = fetch_bars(symbol, period="5d")
        if not bars.empty and len(bars) >= 50:
            bars_cache[symbol] = bars
    print(f"{len(bars_cache)} symbols loaded\n")

    # Use the OLD config (better performer) with $10 cap
    strategy = MomentumSurgeStrategy(
        atr_period=14, atr_stop_multiplier=2.0,
        volume_period=20, volume_multiplier=1.5, roc_min=0.015,
        rsi_min=55.0, rsi_max=80.0,
        hod_proximity=0.03,
        daily_hod_max_drop=0.05,
        max_trades_per_symbol=2,
        second_trade_volume_multiplier=3.0,
        second_trade_roc_min=0.03,
        risk_reward_target=10.0, min_signal_strength=0.5,
    )

    df = run_backtest(strategy, symbols, bars_cache, max_price=10.0)
    if df.empty:
        print("No trades generated!")
        return

    # ── Categorize trades ──────────────────────────────────────────────
    print("=" * 90)
    print("  TRADE DIAGNOSIS (OLD config, $2.50-$10 only)")
    print("=" * 90)

    # Categories
    bad_entry = df[df["max_r"] < 0.5]
    premature_exit = df[(df["max_r"] >= 2.0) & (df["r_multiple"] < 1.0)]
    gave_back = df[(df["max_r"] >= 1.0) & (df["max_r"] < 2.0) & (df["r_multiple"] < 0.5)]
    clean_win = df[df["r_multiple"] >= 1.0]
    clean_loss = df[(df["max_r"] < 1.0) & (df["r_multiple"] < 0)]

    n = len(df)
    print(f"\n  Total trades: {n}")
    print(f"  Bad entries (MaxR < 0.5R):     {len(bad_entry):>3} ({len(bad_entry)/n*100:.0f}%) — never went in our favor")
    print(f"  Premature exits (MaxR≥2R, <1R): {len(premature_exit):>3} ({len(premature_exit)/n*100:.0f}%) — had it, gave it back")
    print(f"  Gave back gains (1-2R avail):   {len(gave_back):>3} ({len(gave_back)/n*100:.0f}%) — modest move, poor capture")
    print(f"  Clean wins (captured ≥1R):      {len(clean_win):>3} ({len(clean_win)/n*100:.0f}%)")
    print(f"  Clean losses (MaxR<1R, lost):   {len(clean_loss):>3} ({len(clean_loss)/n*100:.0f}%)")

    # ── Exit reason breakdown ──────────────────────────────────────────
    print(f"\n  EXIT REASON BREAKDOWN:")
    for reason_key in ["stop_loss", "VWAP", "RSI", "10-bar low", "end_of_data"]:
        mask = df["exit_reason"].str.contains(reason_key, case=False, na=False)
        sub = df[mask]
        if len(sub) > 0:
            avg_r = sub["r_multiple"].mean()
            avg_max_r = sub["max_r"].mean()
            avg_left = sub["left_on_table_r"].mean()
            wins = len(sub[sub["r_multiple"] > 0])
            print(f"    {reason_key:<15}: {len(sub):>3} trades | "
                  f"avg R: {avg_r:+.2f} | avg MaxR: {avg_max_r:.2f} | "
                  f"avg left on table: {avg_left:.2f}R | "
                  f"win rate: {wins/len(sub)*100:.0f}%")

    # ── Premature exit detail ──────────────────────────────────────────
    if not premature_exit.empty:
        print(f"\n  PREMATURE EXITS — trades that had 2R+ but captured < 1R:")
        print(f"  {'Symbol':<8} {'Entry':>7} {'MaxR':>6} {'ExitR':>6} {'Left':>6} "
              f"{'BarsToMax':>9} {'BarsHeld':>8} {'Exit Reason':<40}")
        print(f"  {'─'*8} {'─'*7} {'─'*6} {'─'*6} {'─'*6} {'─'*9} {'─'*8} {'─'*40}")
        for _, t in premature_exit.iterrows():
            print(f"  {t['symbol']:<8} ${t['entry_price']:>6.2f} "
                  f"{t['max_r']:>5.1f}R {t['r_multiple']:>+5.1f}R "
                  f"{t['left_on_table_r']:>5.1f}R "
                  f"{int(t['bars_to_max']):>9} {int(t['bars_held']):>8} "
                  f"{t['exit_reason']:<40}")

    # ── Bad entry detail ───────────────────────────────────────────────
    if not bad_entry.empty:
        print(f"\n  BAD ENTRIES — trades that never reached 0.5R:")
        print(f"  {'Symbol':<8} {'Entry':>7} {'MaxR':>6} {'RSI':>5} {'Vol':>5} "
              f"{'ROC%':>6} {'VWAP%':>6} {'Exit Reason':<40}")
        print(f"  {'─'*8} {'─'*7} {'─'*6} {'─'*5} {'─'*5} {'─'*6} {'─'*6} {'─'*40}")
        for _, t in bad_entry.iterrows():
            print(f"  {t['symbol']:<8} ${t['entry_price']:>6.2f} "
                  f"{t['max_r']:>5.1f}R "
                  f"{t['rsi']:>5.1f} {t['volume_ratio']:>5.1f} "
                  f"{t['roc_10']:>5.1f}% {t['price_vs_vwap']:>5.1f}% "
                  f"{t['exit_reason']:<40}")

    # ── Entry indicator averages: winners vs losers ────────────────────
    winners = df[df["pnl_pct"] > 0]
    losers = df[df["pnl_pct"] <= 0]
    print(f"\n  ENTRY INDICATORS — WINNERS vs LOSERS:")
    for col, label in [("rsi", "RSI"), ("volume_ratio", "Vol Ratio"),
                       ("roc_10", "ROC %"), ("price_vs_vwap", "VWAP %"),
                       ("signal_strength", "Strength")]:
        w_avg = winners[col].mean() if len(winners) > 0 else 0
        l_avg = losers[col].mean() if len(losers) > 0 else 0
        print(f"    {label:<12}: Winners avg {w_avg:>6.2f} | Losers avg {l_avg:>6.2f}")

    # ── Timing analysis ────────────────────────────────────────────────
    if "entry_time" in df.columns:
        print(f"\n  ENTRY TIME ANALYSIS:")
        df["hour_et"] = df["entry_time"].dt.tz_convert("America/New_York").dt.hour
        for h in sorted(df["hour_et"].unique()):
            sub = df[df["hour_et"] == h]
            wins = len(sub[sub["pnl_pct"] > 0])
            avg_r = sub["r_multiple"].mean()
            print(f"    {h:02d}:00 ET: {len(sub):>3} trades | "
                  f"win rate: {wins/len(sub)*100:>3.0f}% | avg R: {avg_r:+.2f}")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
