"""Daily-bar swing-strategy backtest runner.

CLI:
  python -m scripts.research.swing.run_swing \\
      --start 2025-01-01 --end 2025-06-01 \\
      --symbols-file scripts/research/scan_symbols.txt \\
      [--slip-bps 15] [--n-min 30]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.research.metrics import summarize
from scripts.research.swing.strategies import overnight_drift, short_term_reversal

SWING_STRATEGIES = {
    "overnight_drift": overnight_drift,
    "short_term_reversal": short_term_reversal,
}


def run(client, symbols, start, end, slip_bps: float = 15.0,
        n_min: int = 30) -> list[dict]:
    """Backtest all swing strategies over daily bars for each symbol.

    Parameters
    ----------
    client:   object with get_history(symbol, freq, start, end) -> DataFrame
    symbols:  iterable of ticker strings
    start:    start date (str or date)
    end:      end date (str or date)
    slip_bps: one-way slippage in basis points (applied twice per trade)
    n_min:    minimum trade count before flagging low-N in the table

    Returns
    -------
    list[dict] sorted by expectancy descending, one entry per strategy.
    """
    trades_by_strategy: dict[str, list[float]] = {k: [] for k in SWING_STRATEGIES}
    n_symbols = 0

    for sym in symbols:
        df = client.get_history(sym, "1Day", start, end)
        if df is None or df.empty:
            continue
        n_symbols += 1
        for name, fn in SWING_STRATEGIES.items():
            trades_by_strategy[name].extend(fn(df, slip_bps=slip_bps))

    reports = [summarize(name, returns) for name, returns in trades_by_strategy.items()]
    reports.sort(key=lambda r: r["expectancy"], reverse=True)

    print(f"\nSymbols evaluated: {n_symbols}\n")
    print(f"{'strategy':<22}{'n':>5}{'win%':>7}{'avgW%':>8}{'avgL%':>8}"
          f"{'exp%':>8}{'PF':>6}{'maxDD':>7}")
    for r in reports:
        flag = "" if r["n"] >= n_min else "  (low-N)"
        print(
            f"{r['setup']:<22}{r['n']:>5}{r['win_pct'] * 100:>6.0f}%"
            f"{r['avg_win'] * 100:>7.2f}%{r['avg_loss'] * 100:>7.2f}%"
            f"{r['expectancy'] * 100:>7.2f}%"
            f"{r['profit_factor']:>6.2f}{r['max_drawdown_r'] * 100:>6.1f}%"
            f"{flag}"
        )

    return reports


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Swing-strategy daily-bar backtest."
    )
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--symbols-file", required=True,
                   help="Path to whitespace-delimited ticker file")
    p.add_argument("--slip-bps", type=float, default=15.0,
                   help="One-way slippage in bps (default 15)")
    p.add_argument("--n-min", type=int, default=30,
                   help="Min trades before low-N flag (default 30)")
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
    run(client, symbols, args.start, args.end,
        slip_bps=args.slip_bps, n_min=args.n_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
