#!/usr/bin/env python3
"""
Backtest: 1st/2nd trade logic to prevent faded chop entries.

Compares:
- NO FILTER: No daily HOD filter, no per-symbol trade limit
- HOD ≤5% ONLY: Blanket HOD filter on all trades
- 1st/2nd TRADE: 1st trade near HOD, 2nd trade allowed with stronger momentum, 3rd blocked
"""

import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)

import yfinance as yf
import pandas as pd
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

from src.bot.signals.momentum_surge import MomentumSurgeStrategy


def fetch_bars(symbol: str, period: str = "5d", interval: str = "5m") -> pd.DataFrame:
    """Fetch 5-min bars from yfinance."""
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


def simulate_trade(bars: pd.DataFrame, entry_idx: int, entry_price: float,
                   stop_price: float, strategy) -> dict:
    """Simulate a trade from entry to exit."""
    max_price = entry_price
    exit_price = None
    exit_reason = None
    exit_idx = None

    for i in range(entry_idx + 1, len(bars)):
        cur_price = float(bars["close"].iloc[i])
        cur_high = float(bars["high"].iloc[i])
        cur_low = float(bars["low"].iloc[i])
        max_price = max(max_price, cur_high)

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
                    from src.bot.signals.base import SignalDirection
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


def run_config(name: str, surge: MomentumSurgeStrategy, symbols: list[str],
               bars_cache: dict, min_price: float = 2.50,
               max_trades_per_symbol_per_day: int = 99) -> pd.DataFrame:
    """Run a single config across all symbols with per-symbol trade tracking."""
    all_trades = []

    for symbol in symbols:
        bars = bars_cache.get(symbol)
        if bars is None or len(bars) < 50:
            continue

        # Track trades per day for this symbol
        daily_trade_counts: dict[str, int] = {}  # date_str -> count

        for i in range(40, len(bars) - 5):
            window = bars.iloc[max(0, i - 99):i + 1]
            cur_price = float(bars["close"].iloc[i])

            if min_price > 0 and cur_price < min_price:
                continue

            # Get today's date for this bar
            bar_time = bars.index[i]
            if hasattr(bar_time, 'tz_convert'):
                day_str = str(bar_time.tz_convert("America/New_York").date())
            else:
                day_str = str(bar_time.date())

            symbol_trade_count = daily_trade_counts.get(day_str, 0)

            # Skip if already at max for this symbol today
            if symbol_trade_count >= max_trades_per_symbol_per_day:
                continue

            signal = surge.generate(
                symbol, window, cur_price,
                symbol_trade_count=symbol_trade_count,
            )
            if signal is None:
                continue

            result = simulate_trade(bars, i, signal.entry_price, signal.stop_price, surge)
            result["symbol"] = symbol
            result["signal_strength"] = signal.strength
            result["entry_time"] = bars.index[i]
            result["config"] = name
            result["trade_num"] = symbol_trade_count + 1
            meta = signal.metadata or {}
            result["daily_hod"] = meta.get("daily_hod", 0)
            result["drop_from_hod"] = meta.get("drop_from_hod", 0)
            all_trades.append(result)

            # Record this trade
            daily_trade_counts[day_str] = symbol_trade_count + 1

            # Skip ahead past the trade duration to avoid overlapping signals
            # (can't enter while already in a trade)
            # We advance i by bars_held in the outer loop via a skip mechanism
            # For simplicity, just skip bars_held bars
            # Note: this is approximate since we can't modify loop var in Python
            # Instead, we track "in_trade_until" index
            # ... actually, the dedup below handles this

    if not all_trades:
        return pd.DataFrame()

    # Remove overlapping trades (can't be in two trades at once on same symbol)
    df = pd.DataFrame(all_trades)
    unique = []
    active_until: dict[tuple, int] = {}  # (symbol, day) -> bar index when trade ends

    for _, t in df.iterrows():
        bar_time = t["entry_time"]
        if hasattr(bar_time, 'tz_convert'):
            day_str = str(bar_time.tz_convert("America/New_York").date())
        else:
            day_str = str(bar_time.date())

        key = (t["symbol"], day_str)

        # Find entry bar index in original data
        symbol_bars = bars_cache.get(t["symbol"])
        if symbol_bars is not None:
            try:
                entry_idx = symbol_bars.index.get_loc(bar_time)
                end_idx = entry_idx + t["bars_held"]

                # Skip if we're still in a previous trade
                if key in active_until and entry_idx < active_until[key]:
                    continue

                active_until[key] = end_idx
                unique.append(t)
            except (KeyError, TypeError):
                unique.append(t)
        else:
            unique.append(t)

    return pd.DataFrame(unique) if unique else pd.DataFrame()


def print_summary(name: str, df: pd.DataFrame):
    """Print summary stats for a config."""
    if df.empty:
        print(f"\n  {name}: 0 trades")
        return

    n = len(df)
    wins = len(df[df["pnl_pct"] > 0])
    losses = n - wins
    wr = wins / n * 100
    avg_r = df["r_multiple"].mean()
    total_r = df["r_multiple"].sum()
    avg_pnl = df["pnl_pct"].mean()
    avg_win = df[df["pnl_pct"] > 0]["pnl_pct"].mean() if wins > 0 else 0
    avg_loss = df[df["pnl_pct"] <= 0]["pnl_pct"].mean() if losses > 0 else 0

    # Simulate $250 account
    equity = 250.0
    pnl_total = 0
    for _, t in df.iterrows():
        strength = t.get("signal_strength", 0.5)
        scalar = max(strength, 0.5)
        position_size = equity * 0.90 * scalar
        shares = int(position_size / t["entry_price"]) if t["entry_price"] > 0 else 0
        trade_pnl = shares * (t["exit_price"] - t["entry_price"])
        pnl_total += trade_pnl

    print(f"\n  {name}:")
    print(f"    Trades: {n} | Wins: {wins} | Losses: {losses} | Win Rate: {wr:.0f}%")
    print(f"    Avg P&L: {avg_pnl:+.2f}% | Avg R: {avg_r:+.2f}R | Total R: {total_r:+.1f}R")
    print(f"    Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
    print(f"    Simulated $250 account P&L: ${pnl_total:+.2f}")

    # Trade breakdown by trade number
    if "trade_num" in df.columns:
        for tn in sorted(df["trade_num"].unique()):
            sub = df[df["trade_num"] == tn]
            sw = len(sub[sub["pnl_pct"] > 0])
            sn = len(sub)
            sr = sw / sn * 100 if sn > 0 else 0
            savg = sub["pnl_pct"].mean()
            print(f"    Trade #{int(tn)}: {sn} trades, {sr:.0f}% win rate, avg {savg:+.2f}%")


def main():
    symbols = [
        # Today's movers (Mar 27)
        "ARTL", "VSA", "ONCO", "TURB", "AGX", "KOD", "ADMA", "RBNE", "ADV", "CRE",
        # Yesterday's (Mar 26)
        "AIFF", "EEIQ", "FCHL", "NDLS", "RVI", "PGEN", "BATL", "ARMG", "UGRO",
        "CAR", "NAVN", "RKLZ", "SMCZ",
        # Recent trade history
        "BRAI", "RRGB", "MGN", "USGO", "SPWH", "SHMD",
        "SYNX", "LUNG", "ANTX", "DTCK", "PAVS", "POLA", "WOOF",
        "SPRC", "SHIM", "SVCO",
        # More scanner candidates
        "AIRS", "CTMX", "DSGR", "HBIO", "HCWB", "JZ", "LNZA",
        "NSA", "OPAL", "ULY",
    ]

    period = "4d"

    print("=" * 80)
    print("  OLD vs NEW TUNED STRATEGY BACKTEST")
    print(f"  Period: Last 4 trading days | Timeframe: 5-min bars")
    print(f"  Symbols: {len(symbols)}")
    print("=" * 80)

    bars_cache = {}
    for symbol in symbols:
        print(f"  Fetching {symbol}...", end=" ", flush=True)
        bars = fetch_bars(symbol, period=period)
        if bars.empty or len(bars) < 50:
            print(f"skip ({len(bars)} bars)")
            continue
        bars_cache[symbol] = bars
        print(f"{len(bars)} bars")

    print(f"\n  {len(bars_cache)} symbols with sufficient data")

    # ── Config A: OLD (what was deployed — tight filters) ────────────────
    old_strategy = MomentumSurgeStrategy(
        atr_period=14, atr_stop_multiplier=2.0,
        volume_period=20, volume_multiplier=1.5, roc_min=0.015,
        rsi_min=55.0, rsi_max=80.0,
        hod_proximity=0.03,               # 3% near 20-bar high
        daily_hod_max_drop=0.05,
        max_trades_per_symbol=2,
        second_trade_volume_multiplier=3.0,
        second_trade_roc_min=0.03,
        risk_reward_target=10.0, min_signal_strength=0.5,
    )

    # ── Config B: NEW TUNED (loosened filters) ───────────────────────────
    new_strategy = MomentumSurgeStrategy(
        atr_period=14, atr_stop_multiplier=2.0,
        volume_period=50, volume_multiplier=1.5, roc_min=0.015,
        rsi_min=55.0, rsi_max=90.0,       # wider RSI
        hod_proximity=0.08,               # 8% near 10-bar high
        daily_hod_max_drop=0.05,
        max_trades_per_symbol=2,
        second_trade_volume_multiplier=3.0,
        second_trade_roc_min=0.03,
        risk_reward_target=3.0, min_signal_strength=0.5,  # 3:1 R/R
    )

    df_old = run_config("OLD (3% / 20-bar / 10:1 R/R)", old_strategy, symbols, bars_cache)
    df_new = run_config("NEW (8% / 10-bar / 3:1 R/R)", new_strategy, symbols, bars_cache)

    # ── Results ──────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  RESULTS COMPARISON")
    print("=" * 80)

    print_summary("OLD (3% / 20-bar / 10:1 R/R)", df_old)
    print_summary("NEW TUNED (8% / 10-bar / 3:1 R/R)", df_new)

    # ── Trade log for new tuned ──────────────────────────────────────────
    for label, df in [("OLD", df_old), ("NEW TUNED", df_new)]:
        if not df.empty:
            print(f"\n{'=' * 80}")
            print(f"  TRADE LOG — {label}")
            print("=" * 80)
            print(f"\n  {'Symbol':<8} {'#':>2} {'Entry':>8} {'Exit':>8} {'P&L':>8} "
                  f"{'R':>6} {'MaxR':>6} {'HOD%':>5} {'Exit Reason':<30}")
            print(f"  {'─'*8} {'─'*2} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*5} {'─'*30}")

            for _, t in df.iterrows():
                pnl_str = f"{t['pnl_pct']:+.1f}%"
                tn = int(t.get('trade_num', 1))
                print(f"  {t['symbol']:<8} {tn:>2} ${t['entry_price']:>7.2f} "
                      f"${t['exit_price']:>7.2f} {pnl_str:>8} "
                      f"{t['r_multiple']:>+5.1f}R {t['max_r']:>5.1f}R "
                      f"{t.get('drop_from_hod', 0):>4.1f}% "
                      f"{t['exit_reason']:<30}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
