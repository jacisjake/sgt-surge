"""Portfolio-level ORB/swing backtest runner using fractional-share sizing.

Collects dated trades across all symbols via a pluggable trade_fn callable,
then simulates the full account equity curve with simulate_portfolio.

CLI:
  python -m scripts.research.swing.run_portfolio \\
      --start 2024-01-01 --end 2025-01-01 \\
      --symbols-file scripts/research/scan_symbols.txt \\
      [--strategy short_term_reversal|trend_pullback] \\
      [--start-equity 200] [--risk-pct 0.01] [--max-concurrent 5] \\
      [--slip-bps 15] [--down-days 3] [--hold 5] [--stop-pct 0.05] \\
      [--target-pct 0.10] [--ma 200] [--ma-entry 200] [--ma-exit 50]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from functools import partial
from pathlib import Path
from typing import Callable

from scripts.research.swing.portfolio import simulate_portfolio
from scripts.research.swing.strategies import (
    short_term_reversal_trades,
    trend_pullback_trades,
)


def run(
    client,
    symbols,
    start,
    end,
    trade_fn: Callable,
    starting_equity: float = 200.0,
    risk_pct: float = 0.01,
    max_concurrent: int | None = None,
    min_notional: float = 1.0,
) -> dict:
    """Collect dated trades for each symbol and simulate the portfolio.

    Parameters
    ----------
    client:          object with get_history(symbol, freq, start, end) -> DataFrame
    symbols:         iterable of ticker strings
    start:           start date (str or date)
    end:             end date (str or date)
    trade_fn:        callable(df, symbol) -> list[dict] — strategy to run
    starting_equity: initial account size in dollars
    risk_pct:        fraction of equity risked per trade
    max_concurrent:  max simultaneous open positions (None = unlimited)
    min_notional:    minimum trade notional (skip if below)

    Returns
    -------
    dict from simulate_portfolio with portfolio summary keys.
    """
    all_trades: list[dict] = []

    for sym in symbols:
        df = client.get_history(sym, "1Day", start, end)
        if df is None or df.empty:
            continue
        trades = trade_fn(df, sym)
        all_trades.extend(trades)

    result = simulate_portfolio(
        all_trades,
        starting_equity=starting_equity,
        risk_pct=risk_pct,
        max_concurrent=max_concurrent,
        min_notional=min_notional,
    )
    return result


def _print_summary(result: dict) -> None:
    """Print a human-readable portfolio summary to stdout."""
    print(f"\n{'=' * 50}")
    print(f"{'Portfolio Backtest Summary':^50}")
    print(f"{'=' * 50}")
    print(f"  Starting equity : ${result['starting_equity']:>12.2f}")
    print(f"  Final equity    : ${result['final_equity']:>12.2f}")
    print(f"  Total return    : {result['total_return'] * 100:>10.2f}%")
    print(f"  Max drawdown    : {result['max_drawdown'] * 100:>10.2f}%")
    print(f"  Trades taken    : {result['n_taken']:>10d}")
    print(f"  Trades skipped  : {result['n_skipped']:>10d}")
    if result['n_taken'] > 0:
        print(f"  Best trade $    : ${result['best_trade_pnl']:>12.2f}")
        print(f"  Worst trade $   : ${result['worst_trade_pnl']:>12.2f}")
    print(f"{'=' * 50}\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Portfolio-level swing backtest with fractional sizing."
    )
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--symbols-file", required=True,
                   help="Path to whitespace-delimited ticker file")
    # Strategy selection
    p.add_argument("--strategy", default="short_term_reversal",
                   choices=["short_term_reversal", "trend_pullback"],
                   help="Which strategy to run (default: short_term_reversal)")
    # Portfolio sizing
    p.add_argument("--start-equity", type=float, default=200.0,
                   help="Starting account equity in dollars (default 200)")
    p.add_argument("--risk-pct", type=float, default=0.01,
                   help="Fraction of equity risked per trade (default 0.01)")
    p.add_argument("--max-concurrent", type=int, default=None,
                   help="Max concurrent open positions (default: unlimited)")
    p.add_argument("--min-notional", type=float, default=1.0,
                   help="Minimum trade notional in dollars (default 1.0)")
    # Slippage
    p.add_argument("--slip-bps", type=float, default=15.0,
                   help="One-way slippage in bps (default 15)")
    # Shared knobs
    p.add_argument("--down-days", type=int, default=3)
    p.add_argument("--stop-pct", type=float, default=0.05)
    # short_term_reversal knobs
    p.add_argument("--hold", type=int, default=5)
    p.add_argument("--target-pct", type=float, default=0.10)
    p.add_argument("--ma", type=int, default=200,
                   help="SMA period for short_term_reversal uptrend filter (default 200)")
    # trend_pullback knobs
    p.add_argument("--ma-entry", type=int, default=200,
                   help="SMA period for trend_pullback uptrend filter (default 200)")
    p.add_argument("--ma-exit", type=int, default=50,
                   help="SMA period for trend_pullback exit filter (default 50)")
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

    if args.strategy == "short_term_reversal":
        trade_fn = partial(
            short_term_reversal_trades,
            down_days=args.down_days,
            hold=args.hold,
            stop_pct=args.stop_pct,
            target_pct=args.target_pct,
            ma=args.ma,
            slip_bps=args.slip_bps,
        )
    else:  # trend_pullback
        trade_fn = partial(
            trend_pullback_trades,
            down_days=args.down_days,
            ma_entry=args.ma_entry,
            ma_exit=args.ma_exit,
            stop_pct=args.stop_pct,
            slip_bps=args.slip_bps,
        )

    result = run(
        client, symbols,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        trade_fn=trade_fn,
        starting_equity=args.start_equity,
        risk_pct=args.risk_pct,
        max_concurrent=args.max_concurrent,
        min_notional=args.min_notional,
    )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
