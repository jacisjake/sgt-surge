"""Resolve experiment ledger paths for web, cron, and catch-up."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from src.lab.registry import Experiment, load_registry


DEFAULT_PAPER_ID = "breakout_52w_paper"


def paper_ledger_path(
    *,
    state_dir: str | Path = "state",
    experiment_id: str = DEFAULT_PAPER_ID,
    git_path: str = "config/experiments.yaml",
    override_path: str | None = None,
    migrate: bool = True,
) -> Path:
    """Return the active paper ledger path under *state_dir*.

    Preference order:
      1. ``state_dir/experiments/<id>/ledger.json`` (lab path)
      2. ``state_dir/swing_paper_breakout.json`` (legacy)
      3. registry ledger_path / legacy_ledger_path if they exist as-is

    When *migrate* and only legacy exists, copy it to the lab path once.
    """
    state_dir = Path(state_dir)
    lab_path = state_dir / "experiments" / experiment_id / "ledger.json"
    legacy_path = state_dir / "swing_paper_breakout.json"

    if migrate and legacy_path.exists() and not lab_path.exists():
        lab_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, lab_path)

    if lab_path.exists():
        return lab_path
    if legacy_path.exists():
        return legacy_path

    # Fall back to registry absolute/relative paths (container cwd = /app)
    try:
        ov = override_path or str(state_dir / "experiments" / "overrides.yaml")
        reg = load_registry(git_path, ov)
        exp = reg[experiment_id]
        if migrate:
            maybe_migrate_legacy(exp, state_dir=state_dir)
        for p in (Path(exp.ledger_path), Path(exp.legacy_ledger_path or "")):
            if str(p) and p.exists():
                return p
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass

    return lab_path  # preferred location even if not yet created


def maybe_migrate_legacy(exp: Experiment, *, state_dir: Path | str = "state") -> Optional[Path]:
    """Copy legacy → ledger_path if new missing and legacy exists. Returns new path or None."""
    if not exp.legacy_ledger_path:
        return None
    state_dir = Path(state_dir)
    legacy = Path(exp.legacy_ledger_path)
    target = Path(exp.ledger_path)

    # Also consider basename under state_dir (tests / local layouts)
    if not legacy.exists():
        cand = state_dir / Path(exp.legacy_ledger_path).name
        if cand.exists():
            legacy = cand
    if not target.is_absolute() and not target.exists():
        # remap state/experiments/... under state_dir when cwd differs
        remapped = state_dir / "experiments" / exp.id / "ledger.json"
        if remapped != target:
            target = remapped if not Path(exp.ledger_path).exists() else target

    if target.exists():
        return target
    if legacy.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
        return target
    return None
