"""CLI: python -m scripts.lab.run_experiment --id breakout_52w_paper"""
from __future__ import annotations

import argparse
import sys
from datetime import date


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run a registered lab experiment (paper).")
    p.add_argument("--id", required=True, help="Experiment id from config/experiments.yaml")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: last bar date)")
    p.add_argument("--mode", default=None, help="Override mode (default: experiment.mode)")
    p.add_argument("--preview", action="store_true", help="Reserved; paper still steps ledger")
    p.add_argument("--config", default="config/experiments.yaml")
    p.add_argument("--overrides", default="state/experiments/overrides.yaml")
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient
    from src.lab.runners.experiment import ExperimentRunner

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key,
        app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )
    runner = ExperimentRunner(git_path=args.config, override_path=args.overrides)
    result = runner.run(
        args.id,
        client,
        as_of=as_of,
        mode=args.mode,
        preview=args.preview,
        trading_mode=cfg.trading_mode.value if hasattr(cfg.trading_mode, "value") else str(cfg.trading_mode),
    )
    print(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
