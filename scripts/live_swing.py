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


def apply_gate_to_plan(plan, *, gated: bool):
    """Split a plan into (allowed, blocked) when the promote gate refuses.

    A refused gate blocks NEW RISK, never an exit. Returning early on a gate
    refusal would leave open positions with no stop execution — the fractional
    book carries no resting broker stops, so this script is the only thing
    that closes a losing position.
    """
    if not gated:
        return list(plan), []
    allowed = [o for o in plan if o.get("action") == "sell"]
    blocked = [o for o in plan if o.get("action") != "sell"]
    return allowed, blocked


def check_live_gate(experiment_id, *, trading_mode, enable_orb_live,
                    git_path="config/experiments.yaml",
                    override_path="state/experiments/overrides.yaml"):
    """Return None if the registry permits live orders, else the refusal reason.

    live_swing places real fractional orders. Without this the promote gate is
    decorative: a stage=research experiment with no backtest report would still
    trade real money. Fails closed — an unreadable registry refuses.
    """
    from src.lab.registry import assert_can_run, load_registry

    try:
        reg = load_registry(git_path, override_path)
    except (FileNotFoundError, ValueError, OSError) as e:
        return f"registry unreadable: {e}"
    if experiment_id not in reg:
        return f"unknown experiment {experiment_id!r}"
    try:
        assert_can_run(reg, reg[experiment_id], "live", trading_mode,
                       enable_orb_live=enable_orb_live)
    except PermissionError as e:
        return str(e)
    return None


MARKET_CLOSE_ET = dt.time(16, 0)


def resolve_session(bars_by_symbol, *, now_et=None, as_of=None):
    """Return the last COMPLETE daily session to plan on.

    Schwab publishes a bar for the current session as soon as it opens, with
    ``close`` holding the live price. Planning on that evaluates a breakout
    against an unfinished bar, so it is dropped until the session has closed.
    Returns None when nothing complete is available.
    """
    if as_of is not None:
        return as_of
    dates = sorted({ts.date() for df in bars_by_symbol.values() for ts in df.index})
    if not dates:
        return None
    now_et = now_et or dt.datetime.now()
    today = now_et.date()
    if dates[-1] == today and now_et.time() < MARKET_CLOSE_ET:
        return dates[-2] if len(dates) > 1 else None
    return dates[-1]


def reconcile_fill_prices(position_meta, broker_positions, *, tol: float = 1e-6) -> list[dict]:
    """Rewrite audit entry/stop from the broker's actual average fill.

    Orders are placed at 16:05, after the close, so they fill at the next
    session's open — the planned price is not the price paid. The stop is
    re-derived by preserving the *fraction* the ATR model produced, so the
    trade still risks the intended 1% against the price actually paid. The
    planned figures are kept alongside for the record.
    """
    by_sym = {}
    for p in broker_positions or []:
        sym = str(p.get("symbol") or "").upper()
        try:
            avg = float(p.get("avg_entry_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if sym and avg > 0:
            by_sym[sym] = avg

    changed: list[dict] = []
    for sym, meta in (position_meta or {}).items():
        avg = by_sym.get(str(sym).upper())
        if avg is None or not isinstance(meta, dict):
            continue
        planned_entry = meta.get("entry_price")
        if planned_entry is None:
            meta["entry_price"] = avg
            changed.append({"symbol": sym, "planned_entry": None, "actual_entry": avg})
            continue
        planned_entry = float(planned_entry)
        if abs(planned_entry - avg) <= tol or planned_entry <= 0:
            continue

        planned_stop = meta.get("initial_stop")
        meta["planned_entry_price"] = planned_entry
        meta["entry_price"] = avg
        if planned_stop is not None:
            planned_stop = float(planned_stop)
            frac = (planned_entry - planned_stop) / planned_entry
            meta["planned_initial_stop"] = planned_stop
            meta["initial_stop"] = avg * (1.0 - frac)
        changed.append({
            "symbol": sym,
            "planned_entry": planned_entry,
            "actual_entry": avg,
            "planned_stop": planned_stop,
            "actual_stop": meta.get("initial_stop"),
        })
    return changed


def reconcile_audit_meta(position_meta, held_symbols, journal_path, today,
                         *, allow_empty: bool = False) -> list[dict]:
    """Journal and drop meta for positions the broker no longer holds.

    A position can leave the book without passing through this script's sell
    branch — a manual close, the flatten script, or a missed run. Its meta
    would otherwise sit in the audit forever and the trade would vanish from
    the evidence base entirely. The exit price is not recoverable after the
    fact, so it is recorded as null rather than guessed.
    """
    from src.lab.journal import append_closed_trade

    held = {str(s).upper() for s in held_symbols}

    # A broker call that transiently returns nothing would otherwise journal the
    # entire book as closed and wipe every initial_stop. A genuine full flatten
    # pops its own meta, so "broker empty while meta is populated" is far more
    # likely a failed call than a flat account. Refuse unless forced.
    if not held and position_meta and not allow_empty:
        return []

    dropped: list[dict] = []
    for sym in [s for s in list(position_meta) if str(s).upper() not in held]:
        meta = dict(position_meta.pop(sym) or {})
        dropped.append(append_closed_trade(journal_path, {
            "symbol": sym,
            "entry_date": meta.get("entry_date"),
            "exit_date": today.isoformat(),
            "entry_price": meta.get("entry_price"),
            "exit_price": None,
            "qty": None,
            "initial_stop": meta.get("initial_stop"),
            "reason": "reconciled_unknown_exit",
            "regime": meta.get("regime"),
            "r_multiple": None,
        }))
    return dropped


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
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    today = resolve_session(bars, as_of=as_of)
    if today is None:
        print("No complete daily bar available yet — nothing to plan on.")
        return 1

    acct = client.get_account()
    equity, cash = float(acct["equity"]), float(acct["buying_power"])
    audit = load_audit(Path(args.audit_path)) if args.audit_path else {"position_meta": {}}

    # A position can leave the book without passing through the sell branch
    # below (manual close, flatten script, missed run). Journal those before
    # their meta is lost, then drop them.
    _meta = audit.setdefault("position_meta", {})
    _held = {str(p.get("symbol") or "").upper() for p in positions}
    if not _held and _meta and not args.reconcile_empty:
        print(f"[RECONCILE] broker reported 0 positions but audit holds "
              f"{len(_meta)} entries — skipping (looks like a failed call). "
              f"Re-run with --reconcile-empty if the account is genuinely flat.")
    _orphans = reconcile_audit_meta(_meta, _held, Path(args.journal_path), today,
                                    allow_empty=bool(args.reconcile_empty))

    # Orders placed after the close fill at the next open, so the planned price
    # is not the price paid. Correct entry and stop from the actual fill before
    # planning, or exits are evaluated against a stop that was never real.
    _fills = reconcile_fill_prices(_meta, positions)
    for c in _fills:
        if c.get("planned_stop") is not None:
            print(f"[FILL] {c['symbol']}: entry {c['planned_entry']:.4f} -> "
                  f"{c['actual_entry']:.4f}, stop {c['planned_stop']:.4f} -> "
                  f"{c['actual_stop']:.4f}")
        else:
            print(f"[FILL] {c['symbol']}: entry -> {c['actual_entry']:.4f}")
    if (_orphans or _fills) and args.audit_path:
        save_audit(Path(args.audit_path), audit)
    if _orphans:
        print(f"[RECONCILE] journalled and dropped {len(_orphans)} stale meta "
              f"entries: {', '.join(o['symbol'] for o in _orphans)}")

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

    # The promote gate binds here or it binds nowhere — this is the only path
    # that places real orders.
    gate = check_live_gate(
        args.experiment_id,
        trading_mode=cfg.trading_mode.value,
        enable_orb_live=bool(cfg.enable_orb_live),
    )
    gated = bool(gate) and not args.ignore_gate
    if gated:
        plan, blocked = apply_gate_to_plan(plan, gated=True)
        print(f"\n[GATE] promote gate says {gate}")
        print(f"[GATE] blocking {len(blocked)} new entr"
              f"{'y' if len(blocked) == 1 else 'ies'}; exits still run so open "
              f"positions keep their stops.")
        for o in blocked:
            print(f"         BLOCKED buy {o['qty']:.4f} {o['symbol']}")
        print("[GATE] Promote the experiment, or pass --ignore-gate "
              "--gate-reason '...' to allow entries.")
        if not plan:
            print("\nNothing left to place.")
            snapshot["preview"] = False
            snapshot["results"] = []
            snapshot["gate_blocked"] = blocked
            write_json(last_path, snapshot)
            return 0
    if gate and args.ignore_gate:
        if not args.gate_reason:
            print("\nRefusing --live: --ignore-gate requires --gate-reason.")
            return 1
        print(f"\n[GATE OVERRIDE] {gate} — proceeding: {args.gate_reason}")
        audit.setdefault("gate_overrides", []).append({
            "at": today.isoformat(), "gate": gate, "reason": args.gate_reason,
        })
        if args.audit_path:
            save_audit(Path(args.audit_path), audit)

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
    ap.add_argument("--as-of", default=None,
                    help="plan on this session (YYYY-MM-DD) instead of the last complete one")
    ap.add_argument("--reconcile-empty", action="store_true",
                    help="allow reconciling the whole book when the broker reports "
                         "zero positions (confirm the account is really flat)")
    ap.add_argument("--experiment-id", default="breakout_52w_live",
                    help="registry id whose promote gate governs live orders")
    ap.add_argument("--ignore-gate", action="store_true",
                    help="place orders even if the promote gate refuses (recorded in audit)")
    ap.add_argument("--gate-reason", default=None,
                    help="required justification when --ignore-gate is used")
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
