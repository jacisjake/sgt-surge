"""Orchestrate the expectancy harness: reconstruct universe -> fetch/cache 5-min
bars -> run setups -> score -> rank -> report.

CLI:
  python -m scripts.research.run_harness --start 2026-03-01 --end 2026-06-01 \
      --symbols-file scripts/research/scan_symbols.txt [--slip-bps 15] [--gap-min 0.20]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from scripts.research.gapper_universe import DEFAULT_PARAMS, compute_levels, reconstruct
from scripts.research.indicators_ctx import build_context
from scripts.research.metrics import summarize
from scripts.research.setups.first_pullback import FirstPullback
from scripts.research.setups.orb_clean import ORBClean
from scripts.research.setups.pm_high_break import PMHighBreak
from scripts.research.setups.sneaky_pivot import SneakyPivot
from scripts.research.setups.vwap_reclaim import VWAPReclaim

ALL_SETUPS = [ORBClean(), VWAPReclaim(), FirstPullback(), PMHighBreak(), SneakyPivot()]
CACHE_DIR = Path("state/backtest_cache")


def run_setups_on_day(symbol: str, day_bars: pd.DataFrame, slip_bps: float,
                      levels=None) -> dict:
    """Run every setup on one symbol-day. Returns {setup_key: Trade|None}."""
    ctx = build_context(day_bars, levels=levels)
    ctx.symbol = symbol  # consumed by Setup._exit_from
    out = {}
    for setup in ALL_SETUPS:
        try:
            out[setup.key] = setup.evaluate(ctx, slip_bps)
        except Exception:
            out[setup.key] = None
    return out


def _cached_5min(client, symbol: str, day: date, extended: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fp = CACHE_DIR / symbol / f"{day.isoformat()}.parquet"
    if fp.exists():
        return pd.read_parquet(fp)
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    df = client.get_history(symbol, "5Min", start, end, extended_hours=extended)
    if not df.empty:
        fp.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(fp)
    return df


def run(client, symbols, start: date, end: date, params, slip_bps: float) -> list[dict]:
    universe = reconstruct(client, symbols, start, end, params)
    # fetch daily bars once per symbol to compute prior-day/swing levels
    daily_cache: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", start - timedelta(days=30), end)
        if not df.empty:
            daily_cache[sym] = df

    trades_by_setup: dict[str, list[float]] = {s.key: [] for s in ALL_SETUPS}
    n_days = 0
    for iso_day, syms in universe.items():
        d = date.fromisoformat(iso_day)
        for sym in syms:
            bars = _cached_5min(client, sym, d, extended=True)
            if bars.empty:
                continue
            n_days += 1
            lvl = None
            if sym in daily_cache:
                lvl = compute_levels(daily_cache[sym], d)
            for key, trade in run_setups_on_day(sym, bars, slip_bps, levels=lvl).items():
                if trade is not None:
                    trades_by_setup[key].append(trade.r_multiple)
    reports = [summarize(k, rs) for k, rs in trades_by_setup.items()]
    reports.sort(key=lambda r: r["expectancy"], reverse=True)
    print(f"\nReconstructed setup-days evaluated: {n_days}\n")
    print(f"{'setup':<16}{'n':>5}{'win%':>7}{'avgW':>7}{'avgL':>7}"
          f"{'exp(R)':>8}{'PF':>6}{'maxDD':>7}")
    for r in reports:
        flag = "" if r["n"] >= params.get("n_min", 30) else "  (low-N)"
        print(f"{r['setup']:<16}{r['n']:>5}{r['win_pct']*100:>6.0f}%"
              f"{r['avg_win']:>7.2f}{r['avg_loss']:>7.2f}{r['expectancy']:>8.3f}"
              f"{r['profit_factor']:>6.2f}{r['max_drawdown_r']:>7.1f}{flag}")
    return reports


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--symbols-file", required=True)
    p.add_argument("--slip-bps", type=float, default=15.0)
    p.add_argument("--gap-min", type=float, default=DEFAULT_PARAMS["gap_min"])
    p.add_argument("--top-n", type=int, default=DEFAULT_PARAMS["top_n"])
    p.add_argument("--n-min", type=int, default=30)
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text().split() if s.strip()]
    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path,
    )
    params = {**DEFAULT_PARAMS, "gap_min": args.gap_min, "top_n": args.top_n,
              "n_min": args.n_min}
    run(client, symbols, date.fromisoformat(args.start), date.fromisoformat(args.end),
        params, args.slip_bps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
