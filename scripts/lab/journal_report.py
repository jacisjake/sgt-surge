"""CLI: python -m scripts.lab.journal_report [--path ...] [--json]

Reads the closed-trade journal (or a ledger's closed_trades) and reports the
convex-breakout acceptance metrics: payoff ratio, max winner in R, share of
gains from the top three trades, expectancy in R, and expectancy split by
regime. Selection is on skew — total return is deliberately not the headline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PATH = "state/experiments/breakout_52w_live/journal.json"


def _fmt(v, spec="+.2f", none="—"):
    return none if v is None else format(v, spec)


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def render(summary: dict, path: str) -> str:
    s = summary
    L: list[str] = []
    L.append(f"Closed-trade report — {path}")
    L.append("=" * 62)

    if s["n_closed"] == 0:
        L.append("No closed trades recorded yet.")
        L.append("")
        L.append("The journal fills as positions exit. Until then there is no")
        L.append("forward evidence — the backtest report is the only gate.")
        return "\n".join(L)

    L.append(f"  trades           {s['n_closed']}"
             + (f"  ({s['n_unscored']} unscored — no initial stop on record)"
                if s["n_unscored"] else ""))
    L.append(f"  win rate         {_pct(s['win_rate'])}")
    L.append("")
    L.append("  SHAPE  (the acceptance metrics)")
    L.append(f"    max winner       {_fmt(s['max_winner_r'])}R")
    L.append(f"    payoff ratio     {_fmt(s['payoff_ratio'], '.2f')}"
             f"   (mean win {_fmt(s['mean_win_r'])}R / mean loss {_fmt(s['mean_loss_r'])}R)")
    L.append(f"    top-3 share      {_pct(s['top3_share'])} of gross gain")
    L.append(f"    expectancy       {_fmt(s['expectancy_r'])}R per trade")
    L.append(f"    worst loss       {_fmt(s['worst_loss_r'])}R"
             "   (a scaled stop keeps this near −1R)")
    L.append("")

    L.append("  BY REGIME AT ENTRY")
    for key, label in (("risk_on", "risk-on "), ("risk_off", "risk-off"),
                       ("unknown", "unknown ")):
        b = s["by_regime"][key]
        exp = ("—" if b["expectancy_r"] is None
               else f"{_fmt(b['expectancy_r'])}R")
        L.append(f"    {label}  n={b['n']:<4} expectancy {exp}")
    L.append("")

    L.append("  BY EXIT REASON")
    for reason, n in s["by_reason"].items():
        L.append(f"    {reason:<12} {n}")
    L.append("")

    L.append("-" * 62)
    verdict = s["trail_working"]
    if verdict is None:
        L.append("  VERDICT  no scored trades yet.")
    elif verdict:
        L.append(f"  VERDICT  trail is producing a right tail "
                 f"(best {_fmt(s['max_winner_r'])}R ≥ 3R).")
    else:
        L.append(f"  VERDICT  no trade has exceeded 3R (best "
                 f"{_fmt(s['max_winner_r'])}R). The trail is not")
        L.append("           working, whatever the P&L line says.")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Closed-trade skew report")
    p.add_argument("--path", default=DEFAULT_PATH,
                   help=f"journal or ledger JSON (default: {DEFAULT_PATH})")
    p.add_argument("--id", default=None,
                   help="experiment id — shorthand for state/experiments/<id>/journal.json")
    p.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = p.parse_args(argv)

    from src.lab.metrics.journal_report import load_rows, summarize

    path = f"state/experiments/{args.id}/journal.json" if args.id else args.path
    try:
        rows = load_rows(path)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    summary = summarize(rows)
    if args.json:
        print(json.dumps({"path": str(Path(path)), **summary}, indent=2))
    else:
        print(render(summary, str(Path(path))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
