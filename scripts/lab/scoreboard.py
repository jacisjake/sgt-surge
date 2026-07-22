"""CLI: python -m scripts.lab.scoreboard --id breakout_52w_paper"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Paper experiment equity scoreboard vs 1% north-star")
    p.add_argument("--id", required=True, help="Experiment id")
    p.add_argument("--config", default="config/experiments.yaml")
    p.add_argument("--overrides", default="state/experiments/overrides.yaml")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON only")
    args = p.parse_args(argv)

    from src.lab.metrics.daily_equity import scoreboard_for_experiment
    from src.lab.registry import load_registry

    reg = load_registry(args.config, args.overrides)
    if args.id not in reg:
        print(f"Unknown experiment: {args.id}", file=sys.stderr)
        return 1
    board = scoreboard_for_experiment(reg[args.id])
    if args.json:
        print(json.dumps(board, indent=2))
        return 0

    print(f"=== Scoreboard — {board['experiment_id']} ({board['strategy']}) ===")
    print(f"  ledger          : {board['ledger_path']}")
    print(f"  last_date       : {board['last_date']}")
    print(f"  equity          : ${board['equity_realized']:.2f}")
    print(f"  total return    : {board['total_return']*100:.2f}%")
    print(f"  open / closed   : {board['n_open']} / {board['n_closed']}")
    print(f"  max drawdown    : {board['max_drawdown']*100:.2f}%")
    exp = board["expectancy_per_trade"]
    print(f"  expectancy/trade: {exp if exp is None else f'${exp:.4f}'}")
    roll = board["rolling_mean_daily_return"]
    if roll is None:
        print(f"  rolling mean d  : n/a (need equity_curve_daily)")
    else:
        print(f"  rolling mean d  : {roll*100:.3f}%  (window {board['rolling_window']})")
        gap = board["distance_to_goal"]
        print(f"  north-star 1%/d : gap {gap*100:.3f}%  ({board['note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
