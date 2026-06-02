#!/usr/bin/env python3
"""
Backtest the Opening Range Breakout strategy over 5-min bars from Schwab.

Uses the real OpeningRangeBreakout class so the entry logic under test is the
same one the bot runs in production. Bars come from SchwabClient.get_bars.

Exits are simulated:
- Hard stop at OR low
- 2R take-profit
- Breakeven floor at +1R
- Chandelier overlay above +1R: stop = highest_close - chandelier_mult * ATR
- EOD close at 15:55 ET

NOTE: production monitor.py currently implements only the breakeven floor;
chandelier is in the design but not in code. This backtest models the design
intent so we can see what the trailing logic would add.

Usage:
    python scripts/backtest_orb.py                            # today, live lock list
    python scripts/backtest_orb.py --date 2026-05-29          # one historical day
    python scripts/backtest_orb.py --last 5                   # last 5 days w/ history
    python scripts/backtest_orb.py SYM1 SYM2 ...              # explicit symbols

When --date or --last is used, the lock list is loaded from
state/orb_history/{date}.json (written by the bot at OR lock time).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, time as dtime, date
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import pytz

from src.bot.config import get_bot_config
from src.bot.signals.orb import OpeningRangeBreakout
from src.core.schwab_client import SchwabClient

ET = pytz.timezone("America/New_York")
OR_START = dtime(9, 30)
OR_END = dtime(9, 45)
ENTRY_CUTOFF = dtime(15, 15)
EOD_CLOSE = dtime(15, 55)
TARGET_R = 2.0
ATR_PERIOD = 14
CHANDELIER_MULT = 3.0
HISTORY_DIR = PROJECT / "state" / "orb_history"


@dataclass
class TradeResult:
    symbol: str
    or_high: float
    or_low: float
    or_volume: int
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    r_multiple: Optional[float] = None
    pnl_per_share: Optional[float] = None


def fetch_bars(client: SchwabClient, symbol: str, target_date: Optional[date]):
    """Pull recent 5-min bars and filter to a single ET trading date."""
    df = client.get_bars(symbol, timeframe="5Min", limit=400)
    if df.empty:
        return df, target_date
    # SchwabClient.get_bars indexes in UTC; convert to ET for between_time
    df.index = df.index.tz_convert(ET)
    # Pick the date to backtest. If unspecified, use the latest date that has
    # at least one OR-window bar; otherwise use the requested date.
    available_dates = sorted({ts.date() for ts in df.index})
    if target_date is None:
        # latest day with an OR-window bar
        for d in reversed(available_dates):
            day_df = df[df.index.date == d]
            if not day_df.between_time(OR_START, OR_END, inclusive="left").empty:
                target_date = d
                break
        else:
            return df.iloc[0:0], None
    day_df = df[df.index.date == target_date]
    return day_df, target_date


def or_window(df):
    return df.between_time(OR_START, OR_END, inclusive="left")


def trading_window(df):
    return df.between_time(OR_END, EOD_CLOSE, inclusive="left")


def _true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def simulate_exit(
    bars, entry_time: datetime, entry_price: float, initial_stop: float, target: float
) -> tuple[datetime, float, str]:
    """Walk forward bar by bar with progressive trailing stop.

    - Hard stop at OR low until +1R.
    - At +1R, stop ratchets to entry (breakeven floor).
    - Above +1R, chandelier overlay (highest_close - 3 * ATR_14) ratchets
      the stop up monotonically.
    - Take-profit at +2R is checked first each bar.
    - Stop checked before target to be conservative on intrabar hit order.
    """
    forward = bars[bars.index > entry_time]
    if forward.empty:
        return entry_time, entry_price, "no-data"

    initial_R = entry_price - initial_stop
    stop = initial_stop
    highest_close = entry_price
    tr_window: list[float] = []
    prev_close = entry_price

    for ts, bar in forward.iterrows():
        # Update rolling ATR using completed bars BEFORE checking exits
        tr_window.append(_true_range(prev_close, float(bar["high"]), float(bar["low"])))
        if len(tr_window) > ATR_PERIOD:
            tr_window.pop(0)
        atr = sum(tr_window) / len(tr_window) if tr_window else 0.0

        # Intrabar exit checks (stop first by convention)
        if bar["low"] <= stop:
            reason = "trail" if stop > initial_stop else "stop"
            return ts, stop, reason
        if bar["high"] >= target:
            return ts, target, "target"
        if ts.time() >= EOD_CLOSE:
            return ts, float(bar["close"]), "eod"

        # Bar survived: ratchet trailing references
        highest_close = max(highest_close, float(bar["close"]))
        if initial_R > 0:
            r_now = (float(bar["close"]) - entry_price) / initial_R
            if r_now >= 1.0:
                # Breakeven floor
                stop = max(stop, entry_price)
                # Chandelier overlay (only ratchets up)
                if len(tr_window) >= ATR_PERIOD and atr > 0:
                    chandelier = highest_close - CHANDELIER_MULT * atr
                    stop = max(stop, chandelier)
        prev_close = float(bar["close"])

    # Ran out of bars before any exit -- close at last close (EOD partial)
    last_ts = forward.index[-1]
    last_close = float(forward.iloc[-1]["close"])
    return last_ts, last_close, "eod"


def backtest_one(client: SchwabClient, symbol: str, target_date: Optional[date]) -> tuple[TradeResult, Optional[date]]:
    df, used_date = fetch_bars(client, symbol, target_date)
    if df.empty:
        return TradeResult(symbol, 0, 0, 0, exit_reason="no-bars"), used_date

    or_df = or_window(df)
    if or_df.empty:
        return TradeResult(symbol, 0, 0, 0, exit_reason="no-or-bars"), used_date

    or_high = float(or_df["high"].max())
    or_low = float(or_df["low"].min())
    or_vol = int(or_df["volume"].sum())

    strategy = OpeningRangeBreakout(target_r=TARGET_R)
    strategy.register(symbol)
    strategy.state[symbol].or_high = or_high
    strategy.state[symbol].or_low = or_low
    strategy.state[symbol].or_volume = or_vol
    strategy.state[symbol].or_locked = True

    result = TradeResult(symbol, or_high, or_low, or_vol)

    trade_df = trading_window(df)
    # Skip the 9:45-9:50 bar (it overlaps the OR boundary on yfinance; first
    # eligible bar closes at 9:50 ET).
    for ts, bar in trade_df.iterrows():
        if ts.time() >= ENTRY_CUTOFF:
            break
        signal = strategy.on_bar({
            "symbol": symbol,
            "timestamp": ts.isoformat(),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": int(bar["volume"]),
        })
        if signal is not None:
            result.entry_time = ts
            result.entry_price = signal.entry_price
            stop = signal.stop_price
            target = signal.target_price
            exit_ts, exit_px, reason = simulate_exit(
                trade_df, ts, signal.entry_price, stop, target
            )
            result.exit_time = exit_ts
            result.exit_price = exit_px
            result.exit_reason = reason
            r = signal.entry_price - stop
            result.r_multiple = (exit_px - signal.entry_price) / r if r else 0.0
            result.pnl_per_share = exit_px - signal.entry_price
            return result, used_date

    return result, used_date


def load_default_symbols() -> list[str]:
    try:
        with urlopen("https://ut.gitsum.rest/sgt/api/orb", timeout=5) as r:
            data = json.load(r)
        return sorted(s for s, v in data.items() if v.get("or_locked"))
    except Exception as e:
        print(f"could not fetch default watchlist from dashboard: {e}", file=sys.stderr)
        return []


def load_history(target_date: date) -> Optional[dict]:
    path = HISTORY_DIR / f"{target_date.isoformat()}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("state", {})
    except Exception as e:
        print(f"could not read {path}: {e}", file=sys.stderr)
        return None


def available_history_dates() -> list[date]:
    if not HISTORY_DIR.exists():
        return []
    out = []
    for f in HISTORY_DIR.glob("*.json"):
        try:
            out.append(date.fromisoformat(f.stem))
        except ValueError:
            pass
    return sorted(out)


def run_one_day(client: SchwabClient, symbols: list[str], target_date: Optional[date]):
    """Run the backtest for a single (date, symbols) pair and print a table.

    Returns (fired_count, total_R) for aggregation across multiple days.
    """
    print(f"  symbols ({len(symbols)}): {symbols}")
    results = []
    used_dates = set()
    for s in symbols:
        r, ud = backtest_one(client, s, target_date)
        results.append(r)
        if ud is not None:
            used_dates.add(ud)
    if used_dates:
        print(f"  actual date(s) used: {sorted(used_dates)}")
    return print_results(results)


def print_results(results) -> tuple[int, float]:
    print(f"  {'sym':<6} {'or_h':>7} {'or_l':>7} {'or_v':>10} "
          f"{'entry@':>20} {'px':>7} {'exit':>10} {'r':>6} {'$/sh':>7}")
    print("  " + "-" * 98)
    total_r = 0.0
    fired = 0
    for r in results:
        if r.entry_price is None:
            print(f"  {r.symbol:<6} {r.or_high:7.2f} {r.or_low:7.2f} {r.or_volume:10,} "
                  f"{'-':>20} {'-':>7} {r.exit_reason or 'no-fire':>10} "
                  f"{'-':>6} {'-':>7}")
            continue
        fired += 1
        total_r += r.r_multiple or 0
        et = r.entry_time.astimezone(ET).strftime("%Y-%m-%d %H:%M")
        print(f"  {r.symbol:<6} {r.or_high:7.2f} {r.or_low:7.2f} {r.or_volume:10,} "
              f"{et:>20} {r.entry_price:7.2f} {r.exit_reason:>10} "
              f"{r.r_multiple:6.2f} {r.pnl_per_share:7.2f}")
    print("  " + "-" * 98)
    n = len(results)
    avg = (total_r / fired) if fired else 0
    print(f"  {fired}/{n} fired. sum R = {total_r:.2f}, avg R per fired = {avg:.2f}\n")
    return fired, total_r


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="Single YYYY-MM-DD (ET)")
    parser.add_argument("--last", type=int, default=None,
                        help="Run over last N history-file dates and aggregate")
    parser.add_argument("symbols", nargs="*",
                        help="Explicit symbols; otherwise loaded from history or /sgt/api/orb")
    args = parser.parse_args()

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )
    if not client.is_authenticated:
        print("SchwabClient not authenticated — token missing or stale", file=sys.stderr)
        return 1

    # Multi-day path
    if args.last is not None:
        dates = available_history_dates()
        if not dates:
            print(f"no history files in {HISTORY_DIR}", file=sys.stderr)
            return 1
        dates = dates[-args.last:]
        print(f"running last {len(dates)} days from history: {[d.isoformat() for d in dates]}\n")
        grand_fired = 0
        grand_r = 0.0
        days_run = 0
        for d in dates:
            hist = load_history(d) or {}
            day_syms = sorted(hist.keys()) if hist else []
            if not day_syms:
                print(f"=== {d.isoformat()}: history file empty or missing, skipping ===\n")
                continue
            print(f"=== {d.isoformat()} ({len(day_syms)} symbols from history) ===")
            f, r = run_one_day(client, day_syms, d)
            grand_fired += f
            grand_r += r
            days_run += 1
        print(f"AGGREGATE across {days_run} day(s): "
              f"sum R = {grand_r:.2f}, fires = {grand_fired}, "
              f"avg R per fired = {(grand_r / grand_fired) if grand_fired else 0:.2f}")
        return 0

    # Single-day path
    target_date = date.fromisoformat(args.date) if args.date else None
    # Symbol resolution: CLI args > history file for --date > live dashboard
    if args.symbols:
        symbols = args.symbols
        source = "CLI"
    elif target_date is not None:
        hist = load_history(target_date) or {}
        if hist:
            symbols = sorted(hist.keys())
            source = f"history/{target_date.isoformat()}.json"
        else:
            symbols = []
            source = "history (missing)"
    else:
        symbols = load_default_symbols()
        source = "/sgt/api/orb"
    if not symbols:
        print(f"no symbols (source={source})", file=sys.stderr)
        return 1
    print(f"backtesting {len(symbols)} symbols "
          f"(date={target_date or 'auto-latest'}, source={source})")
    run_one_day(client, symbols, target_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
