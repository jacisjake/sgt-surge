"""Load hand-authored playbook and match modules to condition tags."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml


DEFAULT_PLAYBOOK = "config/playbook.yaml"


def load_playbook(path: str | Path = DEFAULT_PLAYBOOK) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"version": 1, "defaults": {}, "modules": []}
    data = yaml.safe_load(p.read_text()) or {}
    data.setdefault("modules", [])
    data.setdefault("defaults", {})
    return data


def match_modules(
    tags: list[str],
    playbook: Optional[dict[str, Any]] = None,
    *,
    path: str | Path = DEFAULT_PLAYBOOK,
) -> list[dict[str, Any]]:
    """Return playbook modules whose when_tags are a subset of *tags*.

    More specific modules (more tags) sort first; stable by file order.
    """
    pb = playbook if playbook is not None else load_playbook(path)
    tagset = set(tags)
    matched: list[tuple[int, int, dict]] = []
    for idx, mod in enumerate(pb.get("modules") or []):
        need = set(mod.get("when_tags") or [])
        if need and need.issubset(tagset):
            # higher specificity first
            matched.append((-len(need), idx, mod))
    matched.sort()
    return [m for _, _, m in matched]


def build_education_payload(
    tags: list[str],
    *,
    playbook_path: str | Path = DEFAULT_PLAYBOOK,
    condition_summary: str = "",
) -> dict[str, Any]:
    """Combine defaults + matched modules for API / agent consumption."""
    pb = load_playbook(playbook_path)
    modules = match_modules(tags, pb)
    return {
        "tags": list(tags),
        "summary": condition_summary,
        "always": list((pb.get("defaults") or {}).get("always_show") or []),
        "modules": modules,
        "primary": modules[0] if modules else None,
        "playbook_version": pb.get("version"),
    }
