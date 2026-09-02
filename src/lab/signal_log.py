"""Append-only record of momentum scanner hits.

A gainers list is ranked on having already moved, so it always looks good. The
only way to know whether appearing on it predicts anything is to timestamp each
first sighting and measure what follows. This module records; it never trades.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

FIELDS = ("change_pct", "volume", "relative_volume", "gap_pct", "prev_close",
          "high_of_day", "low_of_day", "news_count")


def load_hits(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists() or not p.read_text().strip():
        return []
    data = json.loads(p.read_text())
    return data if isinstance(data, list) else []


def _row(cand, observed_at: datetime) -> dict[str, Any]:
    price = float(getattr(cand, "price", 0.0) or 0.0)
    vol = getattr(cand, "volume", None)
    row: dict[str, Any] = {
        "symbol": str(getattr(cand, "symbol", "")).upper(),
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "session": observed_at.date().isoformat(),
        "price": price,
        "dollar_volume": (price * float(vol)) if vol else None,
        "has_catalyst": bool(getattr(cand, "has_catalyst", False)),
        "passes_all_filters": bool(getattr(cand, "passes_all_filters", False)),
    }
    for f in FIELDS:
        v = getattr(cand, f, None)
        row[f] = float(v) if isinstance(v, (int, float)) else v
    return row


def append_hits(path: str | Path, candidates, *,
                observed_at: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Record the first sighting of each symbol per session. Never raises.

    Only the first sighting is kept: the scanner reruns every few minutes and
    a symbol that stays elevated would otherwise dominate the sample.
    """
    observed_at = observed_at or datetime.now()
    session = observed_at.date().isoformat()
    rows = load_hits(path)
    seen = {(r.get("symbol"), r.get("session")) for r in rows}

    added: list[dict[str, Any]] = []
    for cand in candidates or []:
        try:
            row = _row(cand, observed_at)
        except Exception:  # noqa: BLE001 — observation must never break the scan
            continue
        if not row["symbol"] or (row["symbol"], session) in seen:
            continue
        seen.add((row["symbol"], session))
        rows.append(row)
        added.append(row)

    if added:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, indent=2) + "\n")
    return added
