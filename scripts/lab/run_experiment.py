"""CLI: python -m scripts.lab.run_experiment --id breakout_52w_live"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run a registered lab experiment.")
    p.add_argument("--id", required=True, help="Experiment id from config/experiments.yaml")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: last bar date)")
    p.add_argument("--mode", default=None, help="Override mode: backtest|dry_run|live")
    p.add_argument("--preview", action="store_true", help="Plan only (live); no broker submits")
    p.add_argument("--live", action="store_true", help="Allow real submits when stage=live")
    p.add_argument("--config", default="config/experiments.yaml")
    p.add_argument("--overrides", default="state/experiments/overrides.yaml")
    args = p.parse_args(argv)

    from config.settings import TradingMode
    from src.bot.config import get_bot_config
    from src.core.order_executor import OrderExecutor
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
    mode = args.mode
    preview = args.preview or not args.live
    executor = None
    if mode == "live" and args.live:
        if cfg.trading_mode != TradingMode.LIVE:
            print(f"Refusing --live: TRADING_MODE={cfg.trading_mode.value}", file=sys.stderr)
            return 1
        executor = OrderExecutor(client, trading_mode=TradingMode.LIVE)
        preview = False

    result = runner.run(
        args.id,
        client,
        as_of=as_of,
        mode=mode,
        preview=preview if (mode == "live" or args.live) else False,
        trading_mode=cfg.trading_mode.value,
        enable_orb_live=bool(cfg.enable_orb_live),
        executor=executor,
    )

    # Optional alert for live submits
    if mode == "live" and not preview and result.get("results"):
        from src.bot.alerts import send_email_alert
        from scripts.live_swing import order_summary

        subject, body = order_summary(
            result["results"], result.get("date"), float(result.get("equity") or 0)
        )
        send_email_alert(subject, body, cfg)

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
