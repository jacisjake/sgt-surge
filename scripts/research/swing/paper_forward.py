"""Stateful daily paper forward-tester for the 52-week-high breakout strategy.

Simulates fills only — NEVER places a real order.

Core decisions + SimFill live in ``src.lab``; this module remains the CLI and
backward-compatible API for cron / tests.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import portfolio_from_paper
from src.lab.protocol import MarketContext
from src.lab.strategies._common import build_risk_on, is_fresh_breakout, stop_fill_price
from src.lab.strategies.breakout_52w import Breakout52wStrategy

# Re-export for tests / callers that imported helpers from this module
__all__ = [
    "new_state",
    "load_state",
    "save_state",
    "is_fresh_breakout",
    "step",
    "run_once",
    "main",
    "stop_fill_price",
    "build_risk_on",
]


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


def step(
    state: dict,
    bars_by_symbol: dict[str, pd.DataFrame],
    today: datetime.date,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
    risk_on: bool | None = None,
) -> dict:
    """Advance the paper ledger by one trading day via lab Strategy + SimFill.

    Parameters match the historical paper_forward.step API. When *risk_on* is
    None, entries are allowed (no regime gate). When True/False, that value is
    forced for entry gating (exits always run).
    """
    # risk_on None => allow entries; else force override
    risk_on_override = True if risk_on is None else risk_on

    portfolio = portfolio_from_paper(state, today)
    market = MarketContext(bars_by_symbol=bars_by_symbol, extras={}, now=today)
    params = {
        "lookback": lookback,
        "ma_exit": ma_exit,
        "stop_pct": stop_pct,
        "risk_pct": risk_pct,
        "use_regime_gate": False,
        "risk_on_override": risk_on_override,
    }
    intents = Breakout52wStrategy().plan(portfolio, market, params)
    return apply_intents(
        state,
        intents,
        market,
        stop_pct=stop_pct,
        slip_bps=slip_bps,
        snapshot_equity=True,
    )


def run_once(
    client,
    symbols: list[str],
    state_path: str,
    risk_pct: float = 0.01,
    lookback: int = 252,
    ma_exit: int = 50,
    stop_pct: float = 0.08,
    slip_bps: float = 15.0,
    use_regime_gate: bool = True,
    regime_sma: int = 200,
) -> dict:
    """Fetch daily bars and step the paper ledger forward by one day."""
    import datetime as _dt

    today_wall = _dt.date.today()
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

    today = max(df.index[-1].date() for df in bars_by_symbol.values())

    risk_on: bool | None = None
    if use_regime_gate:
        spy = client.get_history("SPY", "1Day", fetch_start, today_wall)
        if spy is None or spy.empty:
            print("  [REGIME] SPY fetch failed — treating as risk-OFF (no new entries).")
            risk_on = False
        else:
            risk_map = build_risk_on(spy, sma_period=regime_sma)
            risk_on = risk_map.get(today, False)
            sma_now = float(spy["close"].rolling(regime_sma).mean().iloc[-1])
            spy_now = float(spy["close"].iloc[-1])
            print(
                f"  [REGIME] SPY {spy_now:.2f} vs SMA{regime_sma} {sma_now:.2f} "
                f"-> {'RISK-ON' if risk_on else 'RISK-OFF (no new entries)'}"
            )

    state = load_state(state_path)
    prev_open = len(state["open_positions"])
    prev_closed = len(state["closed_trades"])

    state = step(
        state,
        bars_by_symbol,
        today,
        risk_pct=risk_pct,
        lookback=lookback,
        ma_exit=ma_exit,
        stop_pct=stop_pct,
        slip_bps=slip_bps,
        risk_on=risk_on,
    )
    save_state(state_path, state)

    equity = state["starting_equity"] + state["realized_pnl"]
    new_opens = state["open_positions"][prev_open:]
    new_closes = state["closed_trades"][prev_closed:]
    print(f"\n=== Paper Forward — {today} ===")
    print(f"  Equity          : ${equity:.2f}")
    print(f"  Open positions  : {len(state['open_positions'])}")
    if new_opens:
        print("  NEW ENTRIES:")
        for pos in new_opens:
            print(
                f"    {pos['symbol']}  entry={pos['entry_price']:.2f}  "
                f"stop={pos['stop_price']:.2f}  notional=${pos['notional']:.2f}"
            )
    if new_closes:
        print("  EXITS:")
        for t in new_closes:
            print(
                f"    {t['symbol']}  exit={t['exit_price']:.2f}  "
                f"pnl=${t['pnl']:.2f}  reason={t['reason']}"
            )
    print()
    return state


def main(argv=None) -> int:
    """CLI for the paper forward-tester."""
    p = argparse.ArgumentParser(
        description="Daily paper forward-tester for 52-week-high breakout strategy."
    )
    p.add_argument("--symbols-file", required=True,
                   help="Path to whitespace-delimited ticker file")
    p.add_argument("--state-file", default="state/experiments/breakout_52w_paper/ledger.json",
                   help="Path to JSON ledger file (lab default; legacy path still readable via migrate)")
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
    p.add_argument("--no-regime-gate", action="store_true",
                   help="Disable the SPY>200dma entry gate (validated config keeps it ON)")
    p.add_argument("--regime-sma", type=int, default=200,
                   help="SMA period for the SPY regime gate (default 200)")
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
        use_regime_gate=not args.no_regime_gate,
        regime_sma=args.regime_sma,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
