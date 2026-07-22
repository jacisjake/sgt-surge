"""Build and persist daily market-education briefs."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.lab.education.conditions import MarketCondition, classify_spy
from src.lab.education.playbook import build_education_payload


def conditions_dir(state_dir: str | Path = "state") -> Path:
    return Path(state_dir) / "lab" / "conditions"


def condition_path(as_of: date, state_dir: str | Path = "state") -> Path:
    return conditions_dir(state_dir) / f"{as_of.isoformat()}.json"


def save_condition_report(report: dict[str, Any], state_dir: str | Path = "state") -> Path:
    as_of = date.fromisoformat(report["condition"]["as_of"])
    path = condition_path(as_of, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


def load_condition_report(
    as_of: Optional[date] = None,
    state_dir: str | Path = "state",
) -> Optional[dict[str, Any]]:
    """Load report for *as_of*, or the latest file if as_of is None."""
    d = conditions_dir(state_dir)
    if as_of is not None:
        p = condition_path(as_of, state_dir)
        if not p.exists():
            return None
        return json.loads(p.read_text())
    if not d.exists():
        return None
    files = sorted(d.glob("????-??-??.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def build_brief(
    spy_df: pd.DataFrame,
    *,
    as_of: Optional[date] = None,
    playbook_path: str = "config/playbook.yaml",
    state_dir: str | Path = "state",
    persist: bool = True,
) -> dict[str, Any]:
    """Classify SPY, attach playbook modules, optionally write JSON."""
    cond: MarketCondition = classify_spy(spy_df, as_of=as_of)
    education = build_education_payload(
        cond.tags,
        playbook_path=playbook_path,
        condition_summary=cond.summary,
    )
    report = {
        "condition": cond.to_dict(),
        "education": education,
        "lab_actions": _lab_actions(education),
        "agent_prompt_path": "config/prompts/market_education.md",
    }
    if persist:
        save_condition_report(report, state_dir=state_dir)
    return report


def build_brief_from_client(
    client,
    *,
    as_of: Optional[date] = None,
    playbook_path: str = "config/playbook.yaml",
    state_dir: str | Path = "state",
    persist: bool = True,
    lookback_days: int = 400,
) -> dict[str, Any]:
    """Fetch SPY history via Schwab client and build brief."""
    today = as_of or date.today()
    start = today - timedelta(days=lookback_days)
    spy = client.get_history("SPY", "1Day", start, today)
    if spy is None:
        spy = pd.DataFrame()
    return build_brief(
        spy,
        as_of=as_of,
        playbook_path=playbook_path,
        state_dir=state_dir,
        persist=persist,
    )


def _lab_actions(education: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten linked experiments / modes into clickable lab actions."""
    actions: list[dict[str, str]] = []
    seen: set[str] = set()
    for mod in education.get("modules") or []:
        for play in mod.get("plays") or []:
            exp = play.get("linked_experiment")
            mode = play.get("mode") or "study"
            if not exp:
                continue
            key = f"{exp}:{mode}"
            if key in seen:
                continue
            seen.add(key)
            if mode == "paper":
                cmd = f"python -m scripts.lab.run_experiment --id {exp}"
            elif mode == "stand_down":
                cmd = f"python -m scripts.lab.scoreboard --id {exp}  # review; no new risk"
            else:
                cmd = f"python -m scripts.lab.scoreboard --id {exp}"
            actions.append(
                {
                    "experiment_id": exp,
                    "mode": mode,
                    "title": play.get("title") or exp,
                    "command": cmd,
                }
            )
    return actions
