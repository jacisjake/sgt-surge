"""Last live-swing / flatten snapshots for the operator dashboard."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LIVE_SWING_LAST = "live_swing_last.json"
FLATTEN_LAST = "flatten_last.json"


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2) + "\n")


def read_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists() or not p.read_text().strip():
        return None
    data = json.loads(p.read_text())
    return data if isinstance(data, dict) else None


def parse_universe_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "n": 0, "exists": False}
    symbols = [s.strip().upper() for s in p.read_text().split() if s.strip()]
    return {"path": str(p), "n": len(symbols), "exists": True}


def load_ops(*, state_dir: str = "state", universe_path: str | None = None) -> dict[str, Any]:
    root = Path(state_dir)
    uni = universe_path or str(root / "universes" / "live.txt")
    return {
        "universe": parse_universe_file(uni),
        "last_live_swing": read_json(root / LIVE_SWING_LAST),
        "last_flatten": read_json(root / FLATTEN_LAST),
    }
