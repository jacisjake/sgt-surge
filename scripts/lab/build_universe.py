"""CLI: python -m scripts.lab.build_universe --out state/universes/liquid_lowprice.txt

Builds a screened lab universe (price band + liquidity + history depth) instead
of the hand-maintained state/breakout_universe.txt.

Two stages:
  1. Candidate discovery — TradingView price-band query, major US exchanges only.
  2. Validation — Schwab daily bars decide median dollar volume and history depth.

The screening predicate itself lives in src/lab/universe.py and is pure, so the
filters are unit-tested without touching either network service.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path


def main(argv=None) -> int:
    from src.lab.universe import DEFAULT_PARAMS

    p = argparse.ArgumentParser(
        description="Build a screened price + liquidity universe for the lab"
    )
    p.add_argument("--price-min", type=float, default=DEFAULT_PARAMS["price_min"])
    p.add_argument("--price-max", type=float, default=DEFAULT_PARAMS["price_max"],
                   help="Ceiling must keep ~$24/position to one whole share")
    p.add_argument("--min-dollar-vol", type=float,
                   default=DEFAULT_PARAMS["min_dollar_vol"],
                   help="Median (not mean) daily close*volume floor")
    p.add_argument("--min-bars", type=int, default=int(DEFAULT_PARAMS["min_bars"]),
                   help="Daily bars required; breakout_52w needs a 252 lookback")
    p.add_argument("--top-n", type=int, default=400,
                   help="Candidates to pull from TradingView before validation")
    p.add_argument("--lookback-days", type=int, default=560,
                   help="Calendar days of history to fetch per candidate")
    p.add_argument("--out", default="state/universes/liquid_lowprice.txt")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the result without writing the file")
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.bot.tradingview_screener import TradingViewScreener
    from src.core.schwab_client import SchwabClient
    from src.lab.universe import median_dollar_volume, screen

    params = {
        "price_min": args.price_min,
        "price_max": args.price_max,
        "min_dollar_vol": args.min_dollar_vol,
        "min_bars": args.min_bars,
    }

    candidates = TradingViewScreener().get_price_band(
        top_n=args.top_n, min_price=args.price_min, max_price=args.price_max
    )
    if not candidates:
        print("No candidates returned from TradingView screener.", file=sys.stderr)
        return 1
    print(f"Candidates in ${args.price_min:.2f}-${args.price_max:.2f}: {len(candidates)}")

    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )

    end = date.today()
    start = end - timedelta(days=args.lookback_days)

    rows: list[dict] = []
    skipped = 0
    for i, c in enumerate(candidates, 1):
        symbol = c["symbol"]
        try:
            df = client.get_history(symbol, "1Day", start, end)
        except Exception as e:  # a bad symbol must not abort the whole build
            skipped += 1
            print(f"  [{i}/{len(candidates)}] {symbol}: history failed ({e})")
            continue
        if df is None or df.empty:
            skipped += 1
            continue
        rows.append({
            "symbol": symbol,
            "last_close": float(df["close"].iloc[-1]),
            "median_dollar_vol": median_dollar_volume(
                df["close"].tolist(), df["volume"].tolist()
            ),
            "n_bars": len(df),
        })

    symbols = screen(rows, params)
    print(f"Validated {len(rows)} (skipped {skipped}) -> {len(symbols)} qualify")
    if not symbols:
        print("Nothing qualified; not writing an empty universe.", file=sys.stderr)
        return 1
    print(" ".join(symbols))

    if args.dry_run:
        print("\n--dry-run: file not written")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(" ".join(symbols) + "\n")
    print(f"\nWrote {len(symbols)} symbols -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
