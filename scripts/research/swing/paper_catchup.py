"""One-off catch-up replay for the breakout_52w paper ledger.

The daily forward-tester (paper_forward.run_once) stalled from 2026-06-19
onward when the Schwab refresh token died, so the ledger's last_date froze
at 2026-06-18 with 8 open positions. run_once only ever steps to the single
latest bar date, so simply resuming would skip evaluating every missed day —
hiding any stops / trend-break exits that should have fired in the gap.

This script fetches the full daily history once, then calls
paper_forward.step() for EACH real trading day strictly after the ledger's
last_date, in ascending order. step()'s own idempotency guard keeps each day
processed exactly once. Trading days are taken from the union of dates present
in the fetched bars, so holidays/weekends are skipped automatically.

Simulates fills only — never places a real order. Run inside the bot container.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys

sys.path.insert(0, "/app")

import pandas as pd  # noqa: E402

from scripts.research.swing.paper_forward import (  # noqa: E402
    load_state,
    save_state,
    step,
)

# Defaults matching the scheduled runner (confirmed: $25 notional = 1% * $200 / 8%).
RISK_PCT = 0.01
LOOKBACK = 252
MA_EXIT = 50
STOP_PCT = 0.08
SLIP_BPS = 15.0

STATE_PATH = "/app/state/swing_paper_breakout.json"
UNIVERSE_PATH = "/app/state/breakout_universe.txt"


def main() -> int:
    from pathlib import Path

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [s.strip().upper() for s in Path(UNIVERSE_PATH).read_text().split() if s.strip()]

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
    )

    today_wall = dt.date.today()
    fetch_start = today_wall - dt.timedelta(days=(LOOKBACK + 30) * 2)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, today_wall)
        if df is not None and not df.empty:
            bars_by_symbol[sym] = df
    if not bars_by_symbol:
        print("No bars fetched — aborting.")
        return 1

    state = load_state(STATE_PATH)
    last_date = state["last_date"]
    if last_date is None:
        print("Ledger has no last_date — use the normal runner, not catch-up.")
        return 1
    last = dt.date.fromisoformat(last_date)

    # Real trading days present in the data, strictly after last_date, ascending.
    all_dates: set[dt.date] = set()
    for df in bars_by_symbol.values():
        all_dates.update(ts.date() for ts in df.index)
    gap_days = sorted(d for d in all_dates if d > last)

    # Back up the ledger before mutating.
    shutil.copyfile(STATE_PATH, STATE_PATH + ".bak")

    print(f"Universe: {len(bars_by_symbol)}/{len(symbols)} symbols with bars")
    print(f"Ledger last_date: {last}  -> replaying {len(gap_days)} trading day(s): "
          f"{gap_days[0] if gap_days else '-'} .. {gap_days[-1] if gap_days else '-'}\n")

    for day in gap_days:
        prev_open = len(state["open_positions"])
        prev_closed = len(state["closed_trades"])
        state = step(
            state, bars_by_symbol, day,
            risk_pct=RISK_PCT, lookback=LOOKBACK, ma_exit=MA_EXIT,
            stop_pct=STOP_PCT, slip_bps=SLIP_BPS,
        )
        new_opens = state["open_positions"][prev_open:]
        new_closes = state["closed_trades"][prev_closed:]
        equity = state["starting_equity"] + state["realized_pnl"]
        flags = []
        for t in new_closes:
            flags.append(f"EXIT {t['symbol']} @{t['exit_price']:.2f} "
                         f"pnl=${t['pnl']:+.2f} ({t['reason']})")
        for p in new_opens:
            flags.append(f"ENTER {p['symbol']} @{p['entry_price']:.2f} "
                         f"notional=${p['notional']:.2f}")
        tag = ("  " + "; ".join(flags)) if flags else "  (no change)"
        print(f"{day}  eq=${equity:.2f} open={len(state['open_positions'])}{tag}")

    save_state(STATE_PATH, state)

    equity = state["starting_equity"] + state["realized_pnl"]
    closed = state["closed_trades"]
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    print("\n=== Catch-up complete ===")
    print(f"  last_date      : {state['last_date']}")
    print(f"  equity         : ${equity:.2f}  (return {((equity/state['starting_equity'])-1)*100:+.1f}%)")
    print(f"  realized_pnl   : ${state['realized_pnl']:+.2f}")
    print(f"  open positions : {len(state['open_positions'])}")
    print(f"  closed trades  : {len(closed)}  (win rate "
          f"{(len(wins)/len(closed)*100) if closed else 0:.0f}%)")
    print(f"  backup written : {STATE_PATH}.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
