"""CLI: python -m scripts.lab.sweep_convex --symbols-file state/universes/liquid_lowprice.txt

Runs the convex-breakout k1×k2 grid on the day-step backtest and selects on
skew — largest winner in R, payoff ratio, top-3 share — never on total return.

Bars are fetched once and cached to --bars-cache, then replayed for every grid
cell; a 12-cell grid must not hit the Schwab API twelve times.

If no pair produces a right tail, that is reported as a finding and nothing is
selected. Requires a valid Schwab token unless --bars-cache is already warm.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

K1_DEFAULT = "1.5,2.0,2.5,3.0"
K2_DEFAULT = "2.5,3.0,4.0"


def _floats(csv: str) -> list[float]:
    return [float(x) for x in csv.split(",") if x.strip()]


def _load_cached_bars(cache: Path):
    import pandas as pd

    bars = {}
    if not cache.exists():
        return bars, None
    for f in sorted(cache.glob("*.json")):
        df = pd.read_json(f, orient="split")
        if df.empty:
            continue
        df.index = pd.to_datetime(df.index)
        bars[f.stem.upper()] = df
    spy = bars.pop("SPY", None)
    return bars, spy


def _fetch_and_cache(symbols, cache: Path, lookback_days: int):
    """Fetch daily bars once and persist them. Needs a live Schwab token."""
    import datetime as dt

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path,
    )
    if not client.is_authenticated:
        raise RuntimeError(
            "Schwab not authenticated — no token. Re-authorize, or point "
            "--bars-cache at a directory already holding cached bars."
        )
    cache.mkdir(parents=True, exist_ok=True)
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    got = 0
    for sym in list(symbols) + ["SPY"]:
        df = client.get_history(sym, "1Day", start, end)
        if df is None or df.empty:
            continue
        df.to_json(cache / f"{sym}.json", orient="split")
        got += 1
    print(f"[BARS] cached {got} symbols -> {cache}")


def render(result: dict) -> str:
    L = ["k1 × k2 sweep — selection on skew, not return", "=" * 78]
    L.append(f"{'k1':>5} {'k2':>5} {'n':>5} {'maxR':>7} {'payoff':>8} "
             f"{'top3':>7} {'expR':>7}  {'return':>8}  note")
    L.append("-" * 78)
    for c in result["surface"]:
        s = c["summary"]
        def f(v, spec=".2f"):
            return "—" if v is None else format(v, spec)
        note = "error" if c.get("error") else ("tail" if s.get("trail_working") else "")
        ret = (c.get("metrics") or {}).get("total_return")
        L.append(
            f"{c['k1']:>5} {c['k2']:>5} {s.get('n_closed', 0):>5} "
            f"{f(s.get('max_winner_r')):>7} {f(s.get('payoff_ratio')):>8} "
            f"{f(s.get('top3_share')):>7} {f(s.get('expectancy_r')):>7}  "
            f"{f(ret):>8}  {note}"
        )
    L.append("-" * 78)
    if result["selected"]:
        sel, s = result["selected"], result["selected_summary"]
        L.append(f"  SELECTED  k1={sel['k1']}  k2={sel['k2']}")
        L.append(f"            max winner {s['max_winner_r']:+.2f}R · "
                 f"payoff {s['payoff_ratio']:.2f} · expectancy {s['expectancy_r']:+.2f}R")
        L.append("            Chosen on skew with sane neighbours. Total return was")
        L.append("            not a selection input.")
    else:
        L.append(f"  NO PAIR SELECTED  ({result['reason']})")
        for line in result["finding"].split(". "):
            if line.strip():
                L.append(f"    {line.strip().rstrip('.')}.")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Convex-breakout k1×k2 sweep")
    p.add_argument("--symbols-file", default="state/universes/liquid_lowprice.txt")
    p.add_argument("--bars-cache", default="state/sweep_bars")
    p.add_argument("--fetch", action="store_true",
                   help="fetch bars from Schwab into the cache before sweeping")
    p.add_argument("--lookback-days", type=int, default=1100)
    p.add_argument("--k1", default=K1_DEFAULT)
    p.add_argument("--k2", default=K2_DEFAULT)
    p.add_argument("--capital", type=float, default=200.0)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--lookback", type=int, default=252)
    p.add_argument("--min-trades", type=int, default=15)
    p.add_argument("--out", default="state/experiments/breakout_52w_live/sweep.json")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    from src.lab.runners.backtest import run_day_step_backtest
    from src.lab.sweep import select_pair, sweep_grid

    cache = Path(args.bars_cache)
    sym_path = Path(args.symbols_file)
    symbols = [s.strip().upper() for s in sym_path.read_text().split()
               if s.strip()] if sym_path.exists() else []

    if args.fetch:
        if not symbols:
            print(f"No symbols in {sym_path} — run scripts.lab.build_universe first.",
                  file=sys.stderr)
            return 1
        try:
            _fetch_and_cache(symbols, cache, args.lookback_days)
        except Exception as e:  # noqa: BLE001
            print(f"Bar fetch failed: {e}", file=sys.stderr)
            return 1

    bars, spy = _load_cached_bars(cache)
    if not bars:
        print(f"No cached bars in {cache}. Re-run with --fetch (needs a live Schwab "
              f"token), or populate the cache first.", file=sys.stderr)
        return 1
    print(f"[BARS] {len(bars)} symbols from {cache}"
          + ("" if spy is not None else "  (no SPY — regime gate will be off)"))

    base = {
        "lookback": args.lookback,
        "risk_pct": args.risk_pct,
        "stop_pct": 0.08,
        "atr_period": 14,
        "stop_min_pct": 0.04,
        "stop_max_pct": 0.15,
        "use_regime_gate": spy is not None,
        "regime_sma": 200,
        "use_ma_exit": False,
        "slip_bps": 15.0,
    }

    def runner(params):
        return run_day_step_backtest(
            "breakout_52w", bars, params, capital=args.capital, spy_df=spy,
        )

    cells = sweep_grid(_floats(args.k1), _floats(args.k2),
                       base_params=base, runner=runner)
    result = select_pair(cells, min_trades=args.min_trades)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str) + "\n")

    print(json.dumps(result, indent=2, default=str) if args.json else render(result))
    print(f"\nSurface written to {out}")
    return 0 if result["selected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
