"""Append-only closed-trade journal for the convex-breakout book."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def r_multiple(entry: float, exit_price: float, initial_stop: float) -> float:
    """(exit − entry) / (entry − initial_stop). 0 when the stop distance is 0."""
    risk = entry - initial_stop
    if risk == 0:
        return 0.0
    return (exit_price - entry) / risk


def append_closed_trade(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one closed trade. Computes r_multiple when initial_stop is present."""
    rec = dict(record)
    if "r_multiple" not in rec and rec.get("initial_stop") is not None:
        rec["r_multiple"] = r_multiple(
            float(rec["entry_price"]),
            float(rec["exit_price"]),
            float(rec["initial_stop"]),
        )
    p = Path(path)
    rows: list[dict[str, Any]] = []
    if p.exists() and p.read_text().strip():
        rows = json.loads(p.read_text())
        if not isinstance(rows, list):
            raise ValueError(f"journal at {p} is not a list")
    rows.append(rec)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2) + "\n")
    return rec
