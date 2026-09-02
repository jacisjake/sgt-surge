"""Sell every long broker position. Preview by default; --live places orders.

Used to flatten the mega-cap swing book at the open before the low-price
convex-breakout universe takes over.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, "/app")


def flatten_plan(positions: list[dict]) -> list[dict]:
    """Pure: one market sell per long lot with qty > 0."""
    plan: list[dict] = []
    for p in positions:
        qty = float(p.get("qty") or 0.0)
        if qty <= 0:
            continue
        price = float(p.get("current_price") or p.get("avg_entry_price") or 0.0)
        plan.append({
            "action": "sell",
            "symbol": str(p["symbol"]).upper(),
            "qty": qty,
            "price": price,
            "notional": qty * price,
            "reason": "flatten",
        })
    return plan


def journal_flatten_results(results, position_meta, journal_path, today) -> list[dict]:
    """Record every filled flatten sell so the trade is not lost.

    Flattening is an exit path like any other. Without this the position
    leaves the book with no R-multiple, exit reason or regime on record.
    """
    from scripts.live_swing import record_closed_trades

    tagged = [{**r, "reason": "flatten"} for r in results]
    return record_closed_trades(tagged, position_meta, journal_path, today)


def _run(args) -> int:
    from config.settings import TradingMode
    from src.bot.config import get_bot_config
    from src.core.order_executor import OrderExecutor
    from src.core.schwab_client import SchwabClient
    from src.lab.fills.broker import execute_plan

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
    )
    if not client.is_authenticated:
        print("Schwab not authenticated — re-auth first.")
        return 1

    positions = client.get_positions()
    acct = client.get_account()
    equity = float(acct["equity"])
    cash = float(acct["buying_power"])
    plan = flatten_plan(positions)

    print(f"=== Flatten plan  (equity ${equity:.2f}, cash ${cash:.2f}) ===")
    from src.lab.ops_snapshot import write_json
    snapshot = {
        "equity": equity,
        "cash": cash,
        "preview": not bool(args.live),
        "plan": plan,
        "results": None,
    }
    if not plan:
        print("  Book already flat.")
        write_json("state/flatten_last.json", snapshot)
        return 0
    for o in plan:
        print(f"  SELL {o['qty']:.4f} {o['symbol']:6} @ ~${o['price']:.2f}  "
              f"(~${o['notional']:.2f})")

    if not args.live:
        print("\nPREVIEW ONLY — no orders placed. Re-run with --live to flatten.")
        write_json("state/flatten_last.json", snapshot)
        return 0

    if cfg.trading_mode != TradingMode.LIVE:
        print(f"\nRefusing --live: bot trading_mode is {cfg.trading_mode.value}, not live.")
        return 1

    print("\nPLACING REAL SELLS...")
    ex = OrderExecutor(client, trading_mode=TradingMode.LIVE)
    results = execute_plan(plan, ex)
    failed = 0
    for r in results:
        err = f"  ({r['error']})" if r.get("error") else ""
        print(f"  {r['status'].upper():9} sell {r['qty']:.4f} {r['symbol']}{err}")
        if r.get("status") != "submitted":
            failed += 1
    # Journal the exits and drop their meta, so a flattened position leaves a
    # trade record instead of an orphaned audit entry.
    from src.lab.runners.live import load_audit, save_audit

    audit_path = Path(args.audit_path)
    audit = load_audit(audit_path)
    meta = audit.setdefault("position_meta", {})
    today = dt.date.today()
    journalled = journal_flatten_results(results, meta, Path(args.journal_path), today)
    for r in results:
        if r.get("status") == "submitted":
            meta.pop(r["symbol"], None)
    if journalled:
        save_audit(audit_path, audit)
        print(f"  journalled {len(journalled)} closed trade(s) -> {args.journal_path}")

    snapshot["preview"] = False
    snapshot["results"] = results
    write_json("state/flatten_last.json", snapshot)
    return 1 if failed else 0



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="PLACE REAL SELLS (default is preview only)")
    ap.add_argument("--journal-path",
                    default="state/experiments/breakout_52w_live/journal.json")
    ap.add_argument("--audit-path",
                    default="state/experiments/breakout_52w_live/live_audit.json")
    return _run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
