"""Operator last-run snapshot for the live dashboard."""
import json
from pathlib import Path

from src.lab.ops_snapshot import load_ops, write_json


def test_load_ops_missing_files_is_empty(tmp_path: Path):
    snap = load_ops(state_dir=str(tmp_path))
    assert snap["last_live_swing"] is None
    assert snap["last_flatten"] is None
    assert snap["universe"]["exists"] is False
    assert snap["universe"]["n"] == 0


def test_load_ops_reads_last_live_and_universe(tmp_path: Path):
    write_json(
        tmp_path / "live_swing_last.json",
        {
            "date": "2026-08-18",
            "preview": False,
            "equity": 194.84,
            "cash": 194.84,
            "plan": [{"action": "buy", "symbol": "ATAI", "qty": 6.68, "reason": "fresh_breakout"}],
            "results": [{"action": "buy", "symbol": "ATAI", "status": "submitted"}],
        },
    )
    (tmp_path / "universes").mkdir()
    (tmp_path / "universes" / "live.txt").write_text("ATAI ET NVDA\n")
    snap = load_ops(state_dir=str(tmp_path))
    assert snap["universe"]["n"] == 3
    assert snap["last_live_swing"]["plan"][0]["symbol"] == "ATAI"
    assert snap["last_live_swing"]["results"][0]["status"] == "submitted"
