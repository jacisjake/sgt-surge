"""CLI: classify tape + attach playbook; optional narrative template.

  python -m scripts.lab.market_brief
  python -m scripts.lab.market_brief --json
  python -m scripts.lab.market_brief --narrative
  python -m scripts.lab.market_brief --as-of 2026-07-22
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


def _narrative(report: dict) -> str:
    """Deterministic template narrative (no LLM) — layer C thin path."""
    cond = report.get("condition") or {}
    edu = report.get("education") or {}
    primary = edu.get("primary") or {}
    lines = [
        f"## Market class — {cond.get('as_of')}",
        f"**Tags:** {', '.join(cond.get('tags') or [])}",
        f"**Confidence:** {cond.get('confidence')}",
        "",
        "### What the tape is saying",
        cond.get("summary") or "(no summary)",
        "",
        "### Plays to study",
    ]
    plays = primary.get("plays") or []
    if primary.get("title"):
        lines.append(f"*Module: {primary.get('title')}*")
        lines.append("")
    if not plays:
        lines.append("_No playbook module matched these tags._")
    for p in plays:
        letter = p.get("letter") or "?"
        lines.append(f"**{letter} — {p.get('title', '')}**  ")
        lines.append(f"{(p.get('body') or '').strip()}")
        lines.append("")
    anti = primary.get("anti_lessons") or []
    if anti:
        lines.append("### Avoid")
        for a in anti:
            lines.append(f"- {a}")
        lines.append("")
    actions = report.get("lab_actions") or []
    if actions:
        lines.append("### Lab actions")
        for a in actions:
            lines.append(f"- `{a.get('command')}`")
        lines.append("")
    always = edu.get("always") or []
    if always:
        lines.append("### Always")
        for a in always:
            lines.append(f"- **{a.get('title')}:** {(a.get('body') or '').strip()}")
    lines.append("")
    lines.append(
        "_Narrative is playbook-templated (no model). "
        "For richer prose, feed --json into config/prompts/market_education.md._"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Market condition brief + education playbook")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: last SPY bar)")
    p.add_argument("--json", action="store_true", help="Print full JSON report")
    p.add_argument("--narrative", action="store_true", help="Print template markdown narrative")
    p.add_argument("--no-persist", action="store_true", help="Do not write state/lab/conditions/")
    p.add_argument("--state-dir", default="state")
    p.add_argument("--playbook", default="config/playbook.yaml")
    p.add_argument(
        "--offline-fixture",
        default=None,
        help="Path to SPY OHLCV CSV/parquet for offline tests (skip Schwab)",
    )
    args = p.parse_args(argv)

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    persist = not args.no_persist

    if args.offline_fixture:
        import pandas as pd

        path = Path(args.offline_fixture)
        if path.suffix == ".parquet":
            spy = pd.read_parquet(path)
        else:
            spy = pd.read_csv(path, parse_dates=True, index_col=0)
        from src.lab.education.report import build_brief

        report = build_brief(
            spy,
            as_of=as_of,
            playbook_path=args.playbook,
            state_dir=args.state_dir,
            persist=persist,
        )
    else:
        from src.bot.config import get_bot_config
        from src.core.schwab_client import SchwabClient
        from src.lab.education.report import build_brief_from_client

        cfg = get_bot_config()
        client = SchwabClient(
            app_key=cfg.schwab_app_key,
            app_secret=cfg.schwab_app_secret,
            callback_url=cfg.schwab_oauth_redirect_uri,
            token_path=cfg.schwab_token_path,
            pinned_account_hash=cfg.schwab_account_hash,
        )
        report = build_brief_from_client(
            client,
            as_of=as_of,
            playbook_path=args.playbook,
            state_dir=args.state_dir,
            persist=persist,
        )

    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    if args.narrative:
        print(_narrative(report))
        return 0

    cond = report["condition"]
    print(f"=== Market brief — {cond['as_of']} ===")
    print(f"  tags:        {', '.join(cond.get('tags') or [])}")
    print(f"  confidence:  {cond.get('confidence')}")
    print(f"  summary:     {cond.get('summary')}")
    primary = (report.get("education") or {}).get("primary")
    if primary:
        print(f"  module:      {primary.get('id')} — {primary.get('title')}")
        for play in primary.get("plays") or []:
            print(f"    {play.get('letter')}. [{play.get('mode')}] {play.get('title')}")
    print("  lab_actions:")
    for a in report.get("lab_actions") or []:
        print(f"    - {a.get('command')}")
    if persist:
        print(f"  wrote:       {args.state_dir}/lab/conditions/{cond['as_of']}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
