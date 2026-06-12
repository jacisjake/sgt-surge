"""Stateful daily paper forward-tester for the 52-week-high breakout strategy.

Simulates fills only — NEVER places a real order.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# State schema helpers
# ---------------------------------------------------------------------------

def new_state(starting_equity: float = 200.0) -> dict:
    """Return a blank ledger dict."""
    return {
        "starting_equity": starting_equity,
        "available_cash": starting_equity,
        "realized_pnl": 0.0,
        "last_date": None,
        "open_positions": [],
        "closed_trades": [],
    }


def load_state(path: str) -> dict:
    """Load ledger from *path*; return new_state() if the file is missing."""
    p = Path(path)
    if not p.exists():
        return new_state()
    with p.open() as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    """Write ledger to *path* as pretty-printed JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def is_fresh_breakout(
    highs: list[float],
    closes: list[float],
    i: int,
    lookback: int,
) -> bool:
    """True iff bar i is the FIRST bar of a new lookback-bar high.

    Conditions:
      1. closes[i] >= max(highs[i-lookback : i])     — current bar at new high
      2. closes[i-1] < max(highs[prev_start : i-1])  — prior bar was NOT at new high

    Guard: prev_start = max(0, i - 1 - lookback).
    """
    # Current bar must clear the lookback-bar high window
    window_cur_max = max(highs[i - lookback: i])
    if closes[i] < window_cur_max:
        return False

    # Prior bar must NOT have been at a new high
    prev_start = max(0, i - 1 - lookback)
    window_prev_max = max(highs[prev_start: i - 1])
    return closes[i - 1] < window_prev_max


# ---------------------------------------------------------------------------
# Core step function
# ---------------------------------------------------------------------------

def step(
    state: dict,
    bars_by_symbol: dict[str, pd.DataFrame],
    today: datetime.date,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> dict:
    """Advance the paper ledger by one trading day.

    Parameters
    ----------
    state          : ledger dict (mutated in-place and returned)
    bars_by_symbol : symbol -> daily DataFrame (cols open/high/low/close,
                     DatetimeIndex), including today's row and >= lookback prior rows
    today          : the trading date to process
    risk_pct       : fraction of equity risked per trade
    lookback       : bars in the 52-week-high lookback window
    ma_exit        : SMA period for the trend-break exit
    stop_pct       : hard-stop distance from entry as a fraction
    slip_bps       : one-way slippage in basis points (applied twice per round-trip)

    Returns the (mutated) state dict.
    """
    # --- IDEMPOTENCY ----------------------------------------------------------
    if state["last_date"] is not None:
        if today <= datetime.date.fromisoformat(state["last_date"]):
            return state

    slip = 2 * slip_bps / 10_000
    equity = state["starting_equity"] + state["realized_pnl"]

    # Helper: find the positional index for today in a DataFrame
    def _today_idx(df: pd.DataFrame) -> int | None:
        dates = [ts.date() for ts in df.index]
        matches = [i for i, d in enumerate(dates) if d == today]
        return matches[0] if matches else None

    # --- EXITS FIRST ----------------------------------------------------------
    remaining_positions = []
    for pos in state["open_positions"]:
        sym = pos["symbol"]
        df = bars_by_symbol.get(sym)
        if df is None or df.empty:
            remaining_positions.append(pos)
            continue

        i = _today_idx(df)
        if i is None:
            remaining_positions.append(pos)
            continue

        closes = df["close"].to_numpy()
        lows = df["low"].to_numpy()
        sma_exit_series = df["close"].rolling(ma_exit).mean()
        sma_exit_today = sma_exit_series.iloc[i]

        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        notional = pos["notional"]

        exit_price = None
        reason = None

        if lows[i] <= stop_price:
            exit_price = stop_price
            reason = "stop"
        elif (not pd.isna(sma_exit_today)) and (closes[i] < sma_exit_today):
            exit_price = closes[i]
            reason = "trend_break"

        if exit_price is not None:
            # pnl = notional * ((exit_price*(1-slip)) / (entry_price*(1+slip)) - 1)
            pnl = notional * ((exit_price * (1 - slip)) / (entry_price * (1 + slip)) - 1)
            state["realized_pnl"] += pnl
            state["available_cash"] += notional + pnl
            equity = state["starting_equity"] + state["realized_pnl"]
            state["closed_trades"].append({
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": today.isoformat(),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
                "reason": reason,
            })
        else:
            remaining_positions.append(pos)

    state["open_positions"] = remaining_positions

    # --- ENTRIES --------------------------------------------------------------
    open_symbols = {p["symbol"] for p in state["open_positions"]}

    for sym, df in bars_by_symbol.items():
        if sym in open_symbols:
            continue
        if df is None or df.empty:
            continue

        i = _today_idx(df)
        if i is None or i < lookback:
            continue

        highs = df["high"].to_numpy()
        closes = df["close"].to_numpy()

        if not is_fresh_breakout(highs, closes, i, lookback):
            continue

        entry_price = closes[i]
        notional = min(risk_pct * equity / stop_pct, state["available_cash"])
        if notional < 1.0:
            continue

        stop_price = entry_price * (1 - stop_pct)
        state["open_positions"].append({
            "symbol": sym,
            "entry_date": today.isoformat(),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "notional": notional,
        })
        state["available_cash"] -= notional
        open_symbols.add(sym)

    state["last_date"] = today.isoformat()
    return state


# ---------------------------------------------------------------------------
# run_once — fetch bars and advance state
# ---------------------------------------------------------------------------

def run_once(
    client,
    symbols: list[str],
    state_path: str,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
) -> dict:
    """Fetch daily bars and step the paper ledger forward by one day.

    Parameters
    ----------
    client     : SchwabClient with get_history(symbol, timeframe, start, end)
    symbols    : list of ticker strings to process
    state_path : path to the JSON ledger file
    Others     : forwarded directly to step()

    Returns the updated state dict.
    """
    import datetime as _dt
    today_wall = _dt.date.today()
    # Fetch bars starting ~2 years ago (generous window for lookback+warmup)
    fetch_start = today_wall - _dt.timedelta(days=(lookback + 30) * 2)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, today_wall)
        if df is not None and not df.empty:
            bars_by_symbol[sym] = df

    if not bars_by_symbol:
        print("No bars fetched — nothing to process.")
        state = load_state(state_path)
        return state

    # today = last bar date across all symbols (the most-recent shared trading day)
    today = max(df.index[-1].date() for df in bars_by_symbol.values())

    state = load_state(state_path)
    prev_open = len(state["open_positions"])
    prev_closed = len(state["closed_trades"])

    state = step(
        state, bars_by_symbol, today,
        risk_pct=risk_pct, lookback=lookback, ma_exit=ma_exit,
        stop_pct=stop_pct, slip_bps=slip_bps,
    )
    save_state(state_path, state)

    # Print summary
    equity = state["starting_equity"] + state["realized_pnl"]
    new_opens = state["open_positions"][prev_open:]
    new_closes = state["closed_trades"][prev_closed:]
    print(f"\n=== Paper Forward — {today} ===")
    print(f"  Equity          : ${equity:.2f}")
    print(f"  Open positions  : {len(state['open_positions'])}")
    if new_opens:
        print("  NEW ENTRIES:")
        for pos in new_opens:
            print(f"    {pos['symbol']}  entry={pos['entry_price']:.2f}  "
                  f"stop={pos['stop_price']:.2f}  notional=${pos['notional']:.2f}")
    if new_closes:
        print("  EXITS:")
        for t in new_closes:
            print(f"    {t['symbol']}  exit={t['exit_price']:.2f}  "
                  f"pnl=${t['pnl']:.2f}  reason={t['reason']}")
    print()
    return state


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """CLI for the paper forward-tester."""
    p = argparse.ArgumentParser(
        description="Daily paper forward-tester for 52-week-high breakout strategy."
    )
    p.add_argument("--symbols-file", required=True,
                   help="Path to whitespace-delimited ticker file")
    p.add_argument("--state-file", default="state/swing_paper_breakout.json",
                   help="Path to JSON ledger file (default: state/swing_paper_breakout.json)")
    p.add_argument("--risk-pct", type=float, default=0.01,
                   help="Fraction of equity risked per trade (default 0.01)")
    p.add_argument("--lookback", type=int, default=252,
                   help="Lookback bars for 52-week-high window (default 252)")
    p.add_argument("--ma-exit", type=int, default=50,
                   help="SMA period for trend-break exit (default 50)")
    p.add_argument("--stop-pct", type=float, default=0.08,
                   help="Hard stop distance from entry as fraction (default 0.08)")
    p.add_argument("--slip-bps", type=float, default=15.0,
                   help="One-way slippage in bps (default 15)")
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [
        s.strip().upper()
        for s in Path(args.symbols_file).read_text().split()
        if s.strip()
    ]
    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
    )
    run_once(
        client=client,
        symbols=symbols,
        state_path=args.state_file,
        risk_pct=args.risk_pct,
        lookback=args.lookback,
        ma_exit=args.ma_exit,
        stop_pct=args.stop_pct,
        slip_bps=args.slip_bps,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
