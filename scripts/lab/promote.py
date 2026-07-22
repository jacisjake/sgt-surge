"""CLI: python -m scripts.lab.promote --check|--to|--demote"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Promote / demote lab experiments")
    p.add_argument("experiment_id", help="Experiment id")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Run soft/hard checks; write promote_check.json")
    g.add_argument("--to", choices=["paper", "live"], help="Promote to stage")
    g.add_argument("--demote", choices=["paper", "research"], help="Demote to stage")
    p.add_argument("--force", action="store_true")
    p.add_argument("--reason", default=None)
    p.add_argument("--backtest-report", default=None)
    p.add_argument("--by", default="operator")
    p.add_argument("--config", default="config/experiments.yaml")
    p.add_argument("--overrides", default="state/experiments/overrides.yaml")
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.lab.promote import (
        check_promotion,
        demote,
        promote_to_live,
        promote_to_paper,
    )
    from src.lab.registry import load_registry

    reg = load_registry(args.config, args.overrides)
    if args.experiment_id not in reg:
        print(f"Unknown experiment: {args.experiment_id}", file=sys.stderr)
        return 1
    exp = reg[args.experiment_id]

    if args.check:
        result = check_promotion(exp)
        out = Path(f"state/experiments/{exp.id}/promote_check.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 2

    if args.to == "paper":
        result = promote_to_paper(
            exp.id,
            git_path=args.config,
            override_path=args.overrides,
            backtest_report=args.backtest_report,
            force=args.force,
            reason=args.reason,
            by=args.by,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    if args.to == "live":
        cfg = get_bot_config()
        result = promote_to_live(
            exp.id,
            git_path=args.config,
            override_path=args.overrides,
            force=args.force,
            reason=args.reason,
            by=args.by,
            enable_orb_live=bool(cfg.enable_orb_live),
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    if args.demote:
        result = demote(
            exp.id,
            to_stage=args.demote,
            git_path=args.config,
            override_path=args.overrides,
            reason=args.reason,
            by=args.by,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
