#!/usr/bin/env python3
"""
Backtest comparison: MACD vs VWAP as momentum confirmation in the Surge strategy.

Runs a parameter sweep across shared conditions (RSI, volume, ROC, HOD proximity)
and confirmation-specific params (MACD periods, VWAP-only) to find the best
configuration for catching momentum surges early.
"""

import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)

from dataclasses import dataclass
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
import numpy as np
from loguru import logger

# Suppress debug noise during backtest
logger.remove()
logger.add(sys.stderr, level="WARNING")

from src.data.indicators import atr, macd, rsi, volume_sma


# ── Configuration ────────────────────────────────────────────────────────

@dataclass
class SurgeConfig:
    """A single parameter set to test."""
    name: str
    confirm_type: str  # "macd" or "vwap"
    # Shared params
    hod_proximity: float = 0.03
    rsi_min: float = 55.0
    rsi_max: float = 80.0
    volume_multiplier: float = 3.0
    roc_min: float = 0.03
    bar_strength_min: float = 0.40
    atr_stop_mult: float = 2.0
    # MACD-specific
    macd_fast: int = 8
    macd_slow: int = 21
    macd_signal: int = 5
    macd_require_line_positive: bool = True  # require MACD line > 0 too?


CONFIGS = [
    # ── Current baseline ─────────────────────────────────────────────
    SurgeConfig("MACD-baseline", "macd"),
    SurgeConfig("VWAP-baseline", "vwap"),

    # ── Loosen shared conditions ─────────────────────────────────────
    SurgeConfig("VWAP-wide-RSI", "vwap", rsi_min=45, rsi_max=85),
    SurgeConfig("VWAP-low-vol", "vwap", volume_multiplier=1.5),
    SurgeConfig("VWAP-low-ROC", "vwap", roc_min=0.015),
    SurgeConfig("VWAP-wide-HOD", "vwap", hod_proximity=0.05),
    SurgeConfig("VWAP-loose-bar", "vwap", bar_strength_min=0.30),

    # ── Loosen shared + MACD ─────────────────────────────────────────
    SurgeConfig("MACD-wide-RSI", "macd", rsi_min=45, rsi_max=85),
    SurgeConfig("MACD-low-vol", "macd", volume_multiplier=1.5),
    SurgeConfig("MACD-low-ROC", "macd", roc_min=0.015),

    # ── Faster MACD periods ──────────────────────────────────────────
    SurgeConfig("MACD-fast(5/13/3)", "macd", macd_fast=5, macd_slow=13, macd_signal=3),
    SurgeConfig("MACD-fast+wideRSI", "macd", macd_fast=5, macd_slow=13, macd_signal=3,
                rsi_min=45, rsi_max=85),
    SurgeConfig("MACD-histOnly", "macd", macd_require_line_positive=False),
    SurgeConfig("MACD-histOnly+fast", "macd", macd_fast=5, macd_slow=13, macd_signal=3,
                macd_require_line_positive=False),

    # ── Combined loosening ───────────────────────────────────────────
    SurgeConfig("VWAP-sensitive", "vwap",
                rsi_min=45, rsi_max=85, volume_multiplier=2.0,
                roc_min=0.02, hod_proximity=0.05),
    SurgeConfig("MACD-sensitive", "macd",
                rsi_min=45, rsi_max=85, volume_multiplier=2.0,
                roc_min=0.02, hod_proximity=0.05,
                macd_fast=5, macd_slow=13, macd_signal=3,
                macd_require_line_positive=False),

    # ── Tighter stop ─────────────────────────────────────────────────
    SurgeConfig("VWAP-sens+tight", "vwap",
                rsi_min=45, rsi_max=85, volume_multiplier=2.0,
                roc_min=0.02, hod_proximity=0.05, atr_stop_mult=1.5),
    SurgeConfig("MACD-sens+tight", "macd",
                rsi_min=45, rsi_max=85, volume_multiplier=2.0,
                roc_min=0.02, hod_proximity=0.05, atr_stop_mult=1.5,
                macd_fast=5, macd_slow=13, macd_signal=3,
                macd_require_line_positive=False),
]


# ── VWAP calculation ─────────────────────────────────────────────────────

def calculate_vwap(bars: pd.DataFrame) -> pd.Series:
    """Calculate cumulative intraday VWAP, reset at each market open."""
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    tp_vol = typical_price * bars["volume"]
    bars_et = bars.index.tz_convert("America/New_York")
    day_groups = bars_et.date
    cum_tp_vol = tp_vol.groupby(day_groups).cumsum()
    cum_vol = bars["volume"].groupby(day_groups).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, float("nan"))
    return vwap


# ── Data fetching ────────────────────────────────────────────────────────

def fetch_bars(symbol: str, period: str = "5d", interval: str = "5m") -> pd.DataFrame:
    """Fetch 5-min bars from yfinance."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)
    if df.empty:
        return df
    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


# ── Entry/exit checks ───────────────────────────────────────────────────

def check_entry(bars: pd.DataFrame, idx: int, cfg: SurgeConfig,
                vwap_series: pd.Series) -> dict | None:
    """Check all entry conditions for a given config. Returns computed values or None."""
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    current = float(close.iloc[idx])

    # 20-bar high
    start = max(0, idx - 19)
    bar_high_20 = float(high.iloc[start:idx + 1].max())

    # 1. HOD proximity
    if bar_high_20 > 0 and (bar_high_20 - current) / bar_high_20 > cfg.hod_proximity:
        return None

    # 2. Confirmation (MACD or VWAP)
    if cfg.confirm_type == "macd":
        sub_close = close.iloc[:idx + 1]
        macd_line, _, histogram = macd(sub_close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        cur_hist = float(histogram.iloc[-1])
        cur_macd = float(macd_line.iloc[-1])
        if cur_hist <= 0:
            return None
        if cfg.macd_require_line_positive and cur_macd <= 0:
            return None
    else:  # vwap
        cur_vwap = float(vwap_series.iloc[idx])
        if np.isnan(cur_vwap) or cur_vwap <= 0 or current <= cur_vwap:
            return None

    # 3. RSI
    rsi_values = rsi(close.iloc[:idx + 1], 14)
    cur_rsi = float(rsi_values.iloc[-1])
    if cur_rsi < cfg.rsi_min or cur_rsi > cfg.rsi_max:
        return None

    # 4. Volume
    avg_vol = volume_sma(volume.iloc[:idx + 1], 20)
    cur_avg_vol = float(avg_vol.iloc[-1])
    cur_vol = float(volume.iloc[idx])
    vol_ratio = cur_vol / cur_avg_vol if cur_avg_vol > 0 else 0
    if vol_ratio < cfg.volume_multiplier:
        return None

    # 5. ROC
    roc = close.iloc[:idx + 1].pct_change(10)
    cur_roc = float(roc.iloc[-1])
    if cur_roc < cfg.roc_min:
        return None

    # 6. Bar strength
    cur_high = float(high.iloc[idx])
    cur_low = float(low.iloc[idx])
    bar_range = cur_high - cur_low
    if bar_range > 0:
        close_pos = (current - cur_low) / bar_range
        if close_pos < cfg.bar_strength_min:
            return None

    # ATR for stop
    atr_values = atr(high.iloc[:idx + 1], low.iloc[:idx + 1], close.iloc[:idx + 1], 14)
    cur_atr = float(atr_values.iloc[-1])

    return {"current": current, "cur_atr": cur_atr}


def check_exit(bars: pd.DataFrame, idx: int, cfg: SurgeConfig,
               vwap_series: pd.Series, entry_price: float) -> tuple[bool, str]:
    """Check exit conditions for a given config."""
    close = bars["close"]
    low = bars["low"]
    current = float(close.iloc[idx])

    # Confirmation-specific exit
    if cfg.confirm_type == "macd":
        sub_close = close.iloc[:idx + 1]
        if len(sub_close) >= 30:
            _, _, histogram = macd(sub_close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
            if len(histogram) >= 2:
                cur_h = float(histogram.iloc[-1])
                prev_h = float(histogram.iloc[-2])
                if cur_h < 0 and prev_h < 0:
                    return True, "MACD hist neg"
    else:  # vwap
        if idx >= 1:
            cur_close = float(close.iloc[idx])
            prev_close = float(close.iloc[idx - 1])
            cur_vwap = float(vwap_series.iloc[idx])
            prev_vwap = float(vwap_series.iloc[idx - 1])
            if (not np.isnan(cur_vwap) and not np.isnan(prev_vwap)
                    and cur_close < cur_vwap and prev_close < prev_vwap):
                return True, "Below VWAP 2 bars"

    # Shared exits
    rsi_values = rsi(close.iloc[:idx + 1], 14)
    cur_rsi = float(rsi_values.iloc[-1])
    if cur_rsi < 40:
        return True, "RSI collapse"

    ten_bar_low = float(low.iloc[max(0, idx - 9):idx + 1].min())
    if current < ten_bar_low:
        return True, "Below 10-bar low"

    return False, ""


# ── Trade simulation ─────────────────────────────────────────────────────

def simulate_trade(bars, entry_idx, entry_price, stop_price, cfg, vwap_series):
    """Simulate from entry to exit."""
    max_price = entry_price

    for i in range(entry_idx + 1, len(bars)):
        cur_low = float(bars["low"].iloc[i])
        cur_high = float(bars["high"].iloc[i])
        cur_close = float(bars["close"].iloc[i])
        max_price = max(max_price, cur_high)

        if cur_low <= stop_price:
            return finish_trade(entry_price, stop_price, stop_price, max_price, i - entry_idx, "stop_loss")

        should_exit, reason = check_exit(bars, i, cfg, vwap_series, entry_price)
        if should_exit:
            return finish_trade(entry_price, stop_price, cur_close, max_price, i - entry_idx, reason)

    last_close = float(bars["close"].iloc[-1])
    return finish_trade(entry_price, stop_price, last_close, max_price, len(bars) - 1 - entry_idx, "end_of_data")


def finish_trade(entry, stop, exit_price, max_price, bars_held, reason):
    risk = entry - stop
    return {
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_price": stop,
        "pnl_pct": (exit_price - entry) / entry * 100,
        "r_multiple": (exit_price - entry) / risk if risk > 0 else 0,
        "max_r": (max_price - entry) / risk if risk > 0 else 0,
        "bars_held": bars_held,
        "exit_reason": reason,
    }


# ── Backtest core ────────────────────────────────────────────────────────

def backtest_config(cfg: SurgeConfig, all_bars: dict[str, pd.DataFrame]) -> list[dict]:
    """Run a single config across all symbols."""
    trades = []

    for symbol, bars in all_bars.items():
        vwap_series = calculate_vwap(bars)
        min_bars = 30
        in_trade = False
        exit_idx = 0

        for i in range(min_bars, len(bars) - 5):
            if in_trade and i < exit_idx:
                continue
            if in_trade and i >= exit_idx:
                in_trade = False

            result = check_entry(bars, i, cfg, vwap_series)
            if result is None:
                continue

            entry_price = result["current"]
            stop_price = entry_price - (result["cur_atr"] * cfg.atr_stop_mult)

            trade = simulate_trade(bars, i, entry_price, stop_price, cfg, vwap_series)
            trade["symbol"] = symbol
            trade["entry_time"] = bars.index[i]
            trades.append(trade)
            in_trade = True
            exit_idx = i + trade["bars_held"]

    # Deduplicate: first per day per symbol
    seen = set()
    unique = []
    for t in trades:
        day = t["entry_time"].date()
        key = (t["symbol"], day)
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return unique


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    symbols = [
        # Actual scanner watchlist hits (from server logs)
        "ALOY", "ANNA", "AVXX", "BATL", "BWET", "CVU",
        "EHLD", "HMR", "ICON", "INDO", "LNKS", "MARPS",
        "MOBX", "MVO", "MXC", "NPT", "PLUL", "RCAX",
        "RPGL", "RYOJ", "SPWH", "STAK", "TMDE", "TOPS",
        "TPET", "UFG", "USEG", "ZD",
        # Our actual trades
        "BRAI", "RRGB", "MGN", "USGO",
    ]

    print("=" * 100)
    print("  PARAMETER SWEEP — Momentum Surge Strategy")
    print(f"  Period: Last 5 trading days | Timeframe: 5-min bars | {len(CONFIGS)} configurations")
    print("=" * 100)

    # Fetch all data once
    all_bars: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        print(f"  Fetching {symbol}...", end=" ", flush=True)
        bars = fetch_bars(symbol)
        if bars.empty or len(bars) < 50:
            print(f"skip ({len(bars) if not bars.empty else 0} bars)")
            continue
        all_bars[symbol] = bars
        print(f"{len(bars)} bars")

    print(f"\n  {len(all_bars)} symbols loaded. Running {len(CONFIGS)} configurations...\n")

    # Run all configs
    results = []
    for cfg in CONFIGS:
        trades = backtest_config(cfg, all_bars)
        n = len(trades)
        wins = sum(1 for t in trades if t["pnl_pct"] > 0)
        win_rate = wins / n * 100 if n else 0
        avg_r = np.mean([t["r_multiple"] for t in trades]) if n else 0
        total_r = sum(t["r_multiple"] for t in trades)
        avg_max_r = np.mean([t["max_r"] for t in trades]) if n else 0
        avg_bars = np.mean([t["bars_held"] for t in trades]) if n else 0
        avg_pnl = np.mean([t["pnl_pct"] for t in trades]) if n else 0

        # First entry time across all symbols
        first_entries = {}
        for t in trades:
            sym = t["symbol"]
            if sym not in first_entries or t["entry_time"] < first_entries[sym]:
                first_entries[sym] = t["entry_time"]

        results.append({
            "name": cfg.name,
            "type": cfg.confirm_type.upper(),
            "signals": n,
            "wins": wins,
            "win_rate": win_rate,
            "avg_r": avg_r,
            "total_r": total_r,
            "avg_max_r": avg_max_r,
            "avg_bars": avg_bars,
            "avg_pnl": avg_pnl,
            "trades": trades,
            "first_entries": first_entries,
            "cfg": cfg,
        })

    # ── Results table ────────────────────────────────────────────────────
    print("=" * 100)
    print("  PARAMETER SWEEP RESULTS (sorted by Total R)")
    print("=" * 100)

    header = (f"  {'Config':<22} {'Type':>5} {'Sigs':>5} {'Wins':>5} {'WR%':>5} "
              f"{'AvgR':>7} {'TotR':>7} {'MaxR':>6} {'Bars':>5} {'AvgP&L':>8}")
    print(header)
    print(f"  {'─'*22} {'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*7} {'─'*7} {'─'*6} {'─'*5} {'─'*8}")

    # Sort by total R descending
    results.sort(key=lambda r: r["total_r"], reverse=True)

    for r in results:
        pnl_str = f"{r['avg_pnl']:+.1f}%"
        print(f"  {r['name']:<22} {r['type']:>5} {r['signals']:>5} {r['wins']:>5} "
              f"{r['win_rate']:>4.0f}% {r['avg_r']:>+6.2f}R {r['total_r']:>+6.1f}R "
              f"{r['avg_max_r']:>5.1f}R {r['avg_bars']:>5.1f} {pnl_str:>8}")

    # ── Detailed view of top 3 ──────────────────────────────────────────
    print("\n" + "=" * 100)
    print("  TOP 3 CONFIGURATIONS — TRADE DETAILS")
    print("=" * 100)

    for rank, r in enumerate(results[:3], 1):
        cfg = r["cfg"]
        print(f"\n  #{rank} {r['name']} ({r['type']}) — "
              f"{r['signals']} signals, {r['win_rate']:.0f}% WR, {r['total_r']:+.1f}R")
        diffs = []
        base = CONFIGS[0] if cfg.confirm_type == "macd" else CONFIGS[1]
        if cfg.rsi_min != 55: diffs.append(f"RSI={cfg.rsi_min}-{cfg.rsi_max}")
        if cfg.volume_multiplier != 3: diffs.append(f"vol={cfg.volume_multiplier}x")
        if cfg.roc_min != 0.03: diffs.append(f"ROC={cfg.roc_min:.1%}")
        if cfg.hod_proximity != 0.03: diffs.append(f"HOD={cfg.hod_proximity:.0%}")
        if cfg.bar_strength_min != 0.40: diffs.append(f"barStr={cfg.bar_strength_min}")
        if cfg.atr_stop_mult != 2.0: diffs.append(f"stop={cfg.atr_stop_mult}x ATR")
        if cfg.confirm_type == "macd":
            if cfg.macd_fast != 8: diffs.append(f"MACD={cfg.macd_fast}/{cfg.macd_slow}/{cfg.macd_signal}")
            if not cfg.macd_require_line_positive: diffs.append("histOnly")
        if diffs:
            print(f"    Changes: {', '.join(diffs)}")

        print(f"\n    {'Sym':<7} {'Entry':>8} {'Exit':>8} {'P&L':>8} "
              f"{'R':>6} {'MaxR':>6} {'Bars':>5} {'Time':>12} {'Reason':<20}")
        print(f"    {'─'*7} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*5} {'─'*12} {'─'*20}")

        for t in r["trades"]:
            time_str = t["entry_time"].strftime("%m/%d %H:%M")
            print(f"    {t['symbol']:<7} ${t['entry_price']:>7.2f} ${t['exit_price']:>7.2f} "
                  f"{t['pnl_pct']:>+7.1f}% {t['r_multiple']:>+5.1f}R {t['max_r']:>5.1f}R "
                  f"{t['bars_held']:>5} {time_str:>12} {t['exit_reason']:<20}")

    # ── Entry timing comparison ──────────────────────────────────────────
    best = results[0]
    baseline_macd = next(r for r in results if r["name"] == "MACD-baseline")

    print("\n" + "=" * 100)
    print(f"  ENTRY TIMING: {best['name']} vs MACD-baseline")
    print("=" * 100)

    all_syms = set(best["first_entries"].keys()) | set(baseline_macd["first_entries"].keys())
    if all_syms:
        print(f"\n  {'Symbol':<8} {'Baseline':>20} {'Best':>20} {'Diff':>12}")
        print(f"  {'─'*8} {'─'*20} {'─'*20} {'─'*12}")

        for sym in sorted(all_syms):
            base_t = baseline_macd["first_entries"].get(sym)
            best_t = best["first_entries"].get(sym)
            base_str = base_t.strftime("%m/%d %H:%M") if base_t else "no signal"
            best_str = best_t.strftime("%m/%d %H:%M") if best_t else "no signal"
            if base_t and best_t:
                diff = (base_t - best_t).total_seconds() / 60
                diff_str = f"{diff:+.0f}min"
            elif best_t:
                diff_str = "NEW"
            else:
                diff_str = "LOST"
            print(f"  {sym:<8} {base_str:>20} {best_str:>20} {diff_str:>12}")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
