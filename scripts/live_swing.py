"""Live execution of the regime-gated breakout_52w swing strategy.

Reuses the SAME validated logic as the lab strategy (build_risk_on,
is_fresh_breakout, stop / trend-break exits, 1%-risk sizing) but places REAL
fractional orders through the Schwab executor instead of simulated fills.

Run once daily (cron), after the close. Two-stage by design:
  1. plan_orders(): PURE — given today's data + current broker positions, return
     the exact list of orders it would place. Testable and previewable.
  2. execute_plan(): place those orders via OrderExecutor (fractional by default).

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

from src.lab.fills.broker import intents_to_live_plan  # noqa: E402
from src.lab.protocol import (  # noqa: E402
    MarketContext,
    PortfolioView,
    bar_index_for_date,
)
from src.lab.runners.live import broker_to_views  # noqa: E402
from src.lab.strategies.breakout_52w import Breakout52wStrategy  # noqa: E402



def _today_idx(df: pd.DataFrame, today: dt.date):
    return bar_index_for_date(df, today)


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
    position_meta: dict | None = None,
    k1: float | None = None,
    k2: float = 3.0,
    atr_period: int = 14,
    use_ma_exit: bool = False,
) -> list[dict]:
    """Return the list of orders to place today. Pure — no I/O, no side effects.

    Decisions come from Breakout52wStrategy.plan; this function only converts
    intents into the live order-dict shape and applies cash sizing.
    """
    portfolio = PortfolioView(
        as_of=today,
        equity=equity,
        available_cash=available_cash,
        positions=broker_to_views(
            positions,
            as_of=today,
            stop_pct=stop_pct,
            audit_meta=position_meta or {},
        ),
    )
    extras: dict = {}
    if spy_df is not None:
        extras["SPY"] = spy_df
    market = MarketContext(bars_by_symbol=bars_by_symbol, extras=extras, now=today)
    params = {
        "lookback": lookback,
        "ma_exit": ma_exit,
        "stop_pct": stop_pct,
        "risk_pct": risk_pct,
        "regime_sma": regime_sma,
        "use_regime_gate": use_regime_gate,
        "use_ma_exit": use_ma_exit,
        "k2": k2,
        "atr_period": atr_period,
    }
    if k1 is not None:
        params["k1"] = k1
    intents = Breakout52wStrategy().plan(portfolio, market, params)
    prices: dict[str, float] = {}
    for sym, df in bars_by_symbol.items():
        i = bar_index_for_date(df, today)
        if i is not None:
            prices[sym] = float(df["close"].iloc[i])
    return intents_to_live_plan(
        intents,
        equity=equity,
        available_cash=available_cash,
        stop_pct=stop_pct,
        prices=prices,
        risk_pct_default=risk_pct,
        cash_buffer_pct=0.0,
    )



def execute_plan(plan: list[dict], executor) -> list[dict]:
    """Place each order for real. Returns per-order results; never raises."""
    from loguru import logger
    from src.lab.fills.broker import execute_plan as _lab_execute

    results = _lab_execute(plan, executor)
    for r in results:
        if r.get("status") == "submitted":
            logger.info(
                "[LIVE-SWING] {} {} {} -> {}",
                r["action"], r["qty"], r["symbol"], r.get("result"),
            )
        else:
            logger.error(
                "[LIVE-SWING] {} {} {} REJECTED: {}",
                r["action"], r["qty"], r["symbol"], r.get("error"),
            )
    return results


def record_closed_trades(results, position_meta, journal_path, today) -> list[dict]:
    """Journal every filled sell before its position meta is dropped. Pure I/O.

    A closed position is the only evidence this book produces — R-multiple,
    exit reason and entry regime are unrecoverable once the meta is gone. A
    position with no meta (opened before the audit file existed) is still
    recorded, with the unknowable fields left null rather than guessed.
    """
    from src.lab.journal import append_closed_trade

    written: list[dict] = []
    for r in results:
        if r.get("status") != "submitted" or r.get("action") != "sell":
            continue
        meta = dict((position_meta or {}).get(r["symbol"]) or {})
        entry_price = meta.get("entry_price")
        initial_stop = meta.get("initial_stop")
        record = {
            "symbol": r["symbol"],
            "entry_date": meta.get("entry_date"),
            "exit_date": today.isoformat(),
            "entry_price": entry_price,
            "exit_price": r.get("price"),
            "qty": r.get("qty"),
            "initial_stop": initial_stop,
            "reason": r.get("reason"),
            "regime": meta.get("regime"),
        }
        if entry_price is None or initial_stop is None:
            record["r_multiple"] = None
        written.append(append_closed_trade(journal_path, record))
    return written


def order_summary(results: list[dict], today, equity: float) -> tuple[str, str]:
    """Build the (subject, body) alert email for a live run's orders. Pure.

    Real money moved unattended, so the email always says what the bot did:
    silence means nothing happened; a rejection is flagged in the subject.
    """
    rejected = [r for r in results if r.get("status") == "rejected"]
    n = len(results)
    if rejected:
        subject = f"[sgt-schwab] live_swing: {n} order(s), {len(rejected)} REJECTED"
    else:
        subject = f"[sgt-schwab] live_swing placed {n} order(s) — {today}"
    lines = [f"Live swing run {today} (equity ${equity:.2f}) placed {n} order(s):", ""]
    for r in results:
        line = f"  {r['status'].upper():9} {r['action']} {r['qty']:.4f} {r['symbol']}"
        if r.get("error"):
            line += f"  ({r['error']})"
        lines.append(line)
    if rejected:
        lines += ["", "One or more orders were REJECTED — if these are fractional "
                  "rejections, Schwab may not be accepting fractional for these names. "
                  "Check state/live_swing.log."]
    return subject, "\n".join(lines)


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


def _run(args) -> int:
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

    from src.lab.runners.live import load_audit, save_audit


    symbols_path = Path(args.symbols_file)
    symbols = [
        s.strip().upper()
        for s in symbols_path.read_text().split()
        if s.strip()
    ] if symbols_path.exists() else []
    positions = client.get_positions()
    for p in positions:
        sym = str(p.get("symbol") or "").upper()
        if sym and sym not in symbols:
            symbols.append(sym)
    bars, spy = _fetch(client, symbols, args.lookback)
    if not bars:
        print("No bars fetched — nothing to do.")
        return 1
    today = max(df.index[-1].date() for df in bars.values())

    acct = client.get_account()
    equity, cash = float(acct["equity"]), float(acct["buying_power"])
    audit = load_audit(Path(args.audit_path)) if args.audit_path else {"position_meta": {}}

    regime = None
    if spy is not None and not spy.empty:
        sma = float(spy["close"].rolling(200).mean().iloc[-1])
        spot = float(spy["close"].iloc[-1])
        regime = "RISK-ON" if spot > sma else "RISK-OFF"
        print(f"[REGIME] SPY {spot:.2f} vs SMA200 {sma:.2f} -> {regime}")

    plan = plan_orders(
        positions, bars, spy, equity, cash, today,
        risk_pct=args.risk_pct, lookback=args.lookback,
        ma_exit=args.ma_exit, stop_pct=args.stop_pct,
        position_meta=audit.get("position_meta") or {},
        k1=args.k1, k2=args.k2, atr_period=args.atr_period,
        use_ma_exit=args.use_ma_exit,
    )

    from src.lab.ops_snapshot import write_json
    last_path = Path(getattr(args, "last_run_path", "state/live_swing_last.json"))
    snapshot = {
        "date": today.isoformat(),
        "preview": not bool(args.live),
        "equity": equity,
        "cash": cash,
        "symbols_file": args.symbols_file,
        "regime": regime,
        "plan": plan,
        "results": None,
    }

    print(f"\n=== Live swing plan — {today}  (equity ${equity:.2f}, cash ${cash:.2f}) ===")
    if not plan:
        print("  No orders today.")
        write_json(last_path, snapshot)
        return 0
    for o in plan:
        print(f"  {o['action'].upper():4} {o['qty']:.4f} {o['symbol']:6} "
              f"@ ~${o['price']:.2f}  (~${o['notional']:.2f})  [{o['reason']}]")

    if not args.live:
        print("\nPREVIEW ONLY — no orders placed. Re-run with --live to place them.")
        write_json(last_path, snapshot)
        return 0

    if cfg.trading_mode != TradingMode.LIVE:
        print(f"\nRefusing --live: bot trading_mode is {cfg.trading_mode.value}, not live.")
        return 1

    print("\nPLACING REAL FRACTIONAL ORDERS...")
    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)
    results = execute_plan(plan, ex)
    for r in results:
        print(f"  {r['status'].upper():9} {r['action']} {r['qty']:.4f} {r['symbol']}"
              + (f"  ({r['error']})" if r.get("error") else ""))

    if results:
        from src.bot.alerts import send_email_alert
        subject, body = order_summary(results, today, equity)
        send_email_alert(subject, body, cfg)
        meta = audit.setdefault("position_meta", {})
        record_closed_trades(results, meta, Path(args.journal_path), today)
        for r in results:
            if r.get("status") == "submitted" and r["action"] == "buy":
                meta[r["symbol"]] = {
                    "entry_date": today.isoformat(),
                    "entry_price": r.get("price"),
                    "initial_stop": r.get("stop_price"),
                    "regime": r.get("regime"),
                    "strategy": "breakout_52w",
                }
            if r.get("status") == "submitted" and r["action"] == "sell":
                meta.pop(r["symbol"], None)
        if args.audit_path:
            save_audit(Path(args.audit_path), audit)
    snapshot["preview"] = False
    snapshot["results"] = results
    write_json(last_path, snapshot)
    return 0



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--lookback", type=int, default=252)
    ap.add_argument("--ma-exit", type=int, default=50)
    ap.add_argument("--stop-pct", type=float, default=0.08)
    ap.add_argument("--k1", type=float, default=2.0)
    ap.add_argument("--k2", type=float, default=3.0)
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--use-ma-exit", action="store_true")
    ap.add_argument(
        "--journal-path",
        default="state/experiments/breakout_52w_live/journal.json",
        help="Append-only closed-trade journal (R-multiple, reason, entry regime)",
    )
    ap.add_argument(
        "--audit-path",
        default="state/experiments/breakout_52w_live/live_audit.json",
    )
    ap.add_argument("--live", action="store_true",
                    help="PLACE REAL ORDERS (default is preview only)")

    args = ap.parse_args(argv)

    try:
        return _run(args)
    except Exception as e:  # noqa: BLE001
        # A crashed --live run must not fail silently: it means the ledger/orders
        # didn't advance, usually an expired Schwab token. Alert, then re-raise
        # so cron records the failure too.
        if args.live:
            import traceback as _tb
            try:
                from src.bot.alerts import send_email_alert
                from src.bot.config import get_bot_config
                send_email_alert(
                    "[sgt-schwab] live_swing RUN FAILED",
                    f"The live swing run crashed:\n\n{type(e).__name__}: {e}\n\n"
                    f"{_tb.format_exc()}\n\nLikely an expired Schwab token — "
                    f"re-auth at https://ut.gitsum.rest. Log: state/live_swing.log",
                    get_bot_config(),
                )
            except Exception:  # noqa: BLE001 — alerting must never mask the real error
                pass
        raise


if __name__ == "__main__":
    sys.exit(main())
