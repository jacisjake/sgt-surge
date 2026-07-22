"""Ledger path resolution + migrate."""
from __future__ import annotations

import json
from pathlib import Path

from src.lab.paths import paper_ledger_path


def test_prefers_lab_path_when_present(tmp_path):
    lab = tmp_path / "experiments" / "breakout_52w_paper" / "ledger.json"
    lab.parent.mkdir(parents=True)
    lab.write_text(json.dumps({"starting_equity": 200.0, "realized_pnl": 1.0}))
    (tmp_path / "swing_paper_breakout.json").write_text(json.dumps({"starting_equity": 1}))
    p = paper_ledger_path(state_dir=tmp_path, migrate=False)
    assert p == lab


def test_migrate_legacy_to_lab(tmp_path):
    legacy = tmp_path / "swing_paper_breakout.json"
    legacy.write_text(json.dumps({"starting_equity": 200.0, "realized_pnl": 5.0, "open_positions": []}))
    p = paper_ledger_path(state_dir=tmp_path, migrate=True)
    assert p.name == "ledger.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["realized_pnl"] == 5.0
