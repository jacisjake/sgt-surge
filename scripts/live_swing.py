"""Live execution of the regime-gated breakout_52w swing strategy.

Reuses the SAME validated logic as the paper forward-tester (build_risk_on,
is_fresh_breakout, stop / trend-break exits, 1%-risk sizing) but places REAL
fractional orders through the Schwab executor instead of simulated fills.

Run once daily (cron), after the close. Two-stage by design:
  1. plan_orders(): PURE — given today's data + current broker positions, return
     the exact list of orders it would place. Testable and previewable.
  2. execute_plan(): place those orders via OrderExecutor(allow_fractional=True).

Default is PREVIEW (no orders). --live places real orders and requires the bot's
trading_mode to be 'live'. Fractional support is unproven on Schwab's API — the
first live buy is the empirical test; a rejection is logged/alerted, not fatal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

sys.path.insert(0, "/app")

import pandas as pd  # noqa: E402

from scripts.research.swing.paper_forward import is_fresh_breakout  # noqa: E402
from scripts.research.swing.strategies import build_risk_on  # noqa: E402


def _today_idx(df: pd.DataFrame, today: dt.date):
    for i, ts in enumerate(df.index):
        if ts.date() == today:
            return i
    return None


def plan_orders(
    positions: list[dict],
    bars_by_symbol: dict[str, pd.DataFrame],
    spy_df: pd.DataFrame | None,
    equity: float,
    available_cash: float,
    today: dt.date,
    *,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    regime_sma: int = 200,
    use_regime_gate: bool = True,
) -> list[dict]:
    """Return the list of orders to place today. Pure — no I/O, no side effects.

    Exits are evaluated for every held position regardless of regime (a risk-off
    day must never trap a position). Entries are gated on SPY > SMA(regime_sma).
    """
    plan: list[dict] = []
    held = {p["symbol"] for p in positions}

    # --- EXITS (always evaluated) ------------------------------------------
    for pos in positions:
        sym = pos["symbol"]
        df = bars_by_symbol.get(sym)
        if df is None or df.empty:
            continue
        i = _today_idx(df, today)
        if i is None:
            continue
        stop = pos["avg_entry_price"] * (1 - stop_pct)
        sma_exit = df["close"].rolling(ma_exit).mean().iloc[i]
        low = float(df["low"].iloc[i])
        close = float(df["close"].iloc[i])
        reason = None
        if low <= stop:
            reason = "stop"
        elif (not pd.isna(sma_exit)) and close < sma_exit:
            reason = "trend_break"
        if reason:
            plan.append({
                "action": "sell", "symbol": sym, "qty": pos["qty"],
                "price": close, "notional": pos["qty"] * close, "reason": reason,
            })

    # --- REGIME GATE -------------------------------------------------------
    risk_on = True
    if use_regime_gate:
        if spy_df is None or spy_df.empty:
            risk_on = False
        else:
            risk_on = build_risk_on(spy_df, sma_period=regime_sma).get(today, False)
    if not risk_on:
        return plan  # exits only

    # --- ENTRIES -----------------------------------------------------------
    cash = available_cash
    selling = {p["symbol"] for p in plan}  # don't buy something we're exiting today
    for sym, df in bars_by_symbol.items():
        if sym in held or sym in selling or df is None or df.empty:
            continue
        i = _today_idx(df, today)
        if i is None or i < lookback:
            continue
        highs = df["high"].to_numpy()
        closes = df["close"].to_numpy()
        if not is_fresh_breakout(highs, closes, i, lookback):
            continue
        price = float(closes[i])
        notional = min(risk_pct * equity / stop_pct, cash)
        if notional < 1.0 or price <= 0:
            continue
        qty = round(notional / price, 4)
        if qty <= 0:
            continue
        plan.append({
            "action": "buy", "symbol": sym, "qty": qty,
            "price": price, "notional": qty * price, "reason": "fresh_breakout",
        })
        cash -= notional

    return plan


def execute_plan(plan: list[dict], executor) -> list[dict]:
    """Place each order for real. Returns per-order results; never raises."""
    from loguru import logger
    results = []
    for o in plan:
        try:
            res = executor.execute_market_order(o["symbol"], o["qty"], o["action"],
                                                 wait_for_fill=False)
            results.append({**o, "status": "submitted", "result": str(res)})
            logger.info("[LIVE-SWING] {} {} {} -> {}", o["action"], o["qty"], o["symbol"], res)
        except Exception as e:  # noqa: BLE001
            results.append({**o, "status": "rejected", "error": f"{type(e).__name__}: {e}"})
            logger.error("[LIVE-SWING] {} {} {} REJECTED: {}", o["action"], o["qty"], o["symbol"], e)
    return results


def _fetch(client, symbols, lookback):
    today_wall = dt.date.today()
    start = today_wall - dt.timedelta(days=(lookback + 30) * 2)
    bars = {}
    for s in symbols:
        df = client.get_history(s, "1Day", start, today_wall)
        if df is not None and not df.empty:
            bars[s] = df
    spy = client.get_history("SPY", "1Day", start, today_wall)
    return bars, spy


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--lookback", type=int, default=252)
    ap.add_argument("--ma-exit", type=int, default=50)
    ap.add_argument("--stop-pct", type=float, default=0.08)
    ap.add_argument("--live", action="store_true",
                    help="PLACE REAL ORDERS (default is preview only)")
    args = ap.parse_args(argv)

    from pathlib import Path
    from config.settings import TradingMode
    from src.bot.config import get_bot_config
    from src.core.order_executor import OrderExecutor
    from src.core.schwab_client import SchwabClient

    cfg = get_bot_config()
    client = SchwabClient(app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
                          callback_url=cfg.schwab_oauth_redirect_uri,
                          token_path=cfg.schwab_token_path)
    if not client.is_authenticated:
        print("Schwab not authenticated — re-auth first.")
        return 1

    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text().split() if s.strip()]
    bars, spy = _fetch(client, symbols, args.lookback)
    if not bars:
        print("No bars fetched — nothing to do.")
        return 1
    today = max(df.index[-1].date() for df in bars.values())

    positions = client.get_positions()
    acct = client.get_account()
    equity, cash = float(acct["equity"]), float(acct["buying_power"])

    if spy is not None and not spy.empty:
        sma = float(spy["close"].rolling(200).mean().iloc[-1])
        spot = float(spy["close"].iloc[-1])
        print(f"[REGIME] SPY {spot:.2f} vs SMA200 {sma:.2f} -> "
              f"{'RISK-ON' if spot > sma else 'RISK-OFF'}")

    plan = plan_orders(positions, bars, spy, equity, cash, today,
                       risk_pct=args.risk_pct, lookback=args.lookback,
                       ma_exit=args.ma_exit, stop_pct=args.stop_pct)

    print(f"\n=== Live swing plan — {today}  (equity ${equity:.2f}, cash ${cash:.2f}) ===")
    if not plan:
        print("  No orders today.")
        return 0
    for o in plan:
        print(f"  {o['action'].upper():4} {o['qty']:.4f} {o['symbol']:6} "
              f"@ ~${o['price']:.2f}  (~${o['notional']:.2f})  [{o['reason']}]")

    if not args.live:
        print("\nPREVIEW ONLY — no orders placed. Re-run with --live to place them.")
        return 0

    if cfg.trading_mode != TradingMode.LIVE:
        print(f"\nRefusing --live: bot trading_mode is {cfg.trading_mode.value}, not live.")
        return 1

    print("\nPLACING REAL FRACTIONAL ORDERS...")
    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)
    ex.allow_fractional = True
    for r in execute_plan(plan, ex):
        print(f"  {r['status'].upper():9} {r['action']} {r['qty']:.4f} {r['symbol']}"
              + (f"  ({r['error']})" if r.get("error") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
