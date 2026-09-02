"""Promotion soft/hard gates and overrides (research → live)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lab.promote import (
    check_promotion,
    demote,
    promote_to_live,
    validate_backtest_report,
    write_backtest_report,
)
from src.lab.registry import load_registry


def _mini_registry(tmp_path: Path, stage="research", mode="backtest"):
    git = tmp_path / "exp.yaml"
    git.write_text(
        yaml.dump(
            {
                "version": 1,
                "defaults": {
                    "gates": {
                        "min_closed_trades": 1,
                        "max_drawdown": 0.5,
                        "require_positive_expectancy": True,
                    }
                },
                "experiments": {
                    "x": {
                        "strategy": "breakout_52w",
                        "params": {"lookback": 5},
                        "capital": 200.0,
                        "mode": mode,
                        "stage": stage,
                        "symbols_file": str(tmp_path / "u.txt"),
                        "ledger_path": str(tmp_path / "ledger.json"),
                        "backtest_report_path": str(tmp_path / "bt.json"),
                    }
                },
            }
        )
    )
    (tmp_path / "u.txt").write_text("AAA\n")
    return git, tmp_path / "overrides.yaml"


def _good_report(path, *, n_taken=3, expectancy=0.1, max_drawdown=0.1):
    write_backtest_report(
        path,
        {
            "strategy": "breakout_52w",
            "params": {},
            "window": {"start": "2024-01-01", "end": "2024-06-01"},
            "metrics": {
                "n_taken": n_taken,
                "expectancy": expectancy,
                "max_drawdown": max_drawdown,
                "engine": "day_step",
            },
        },
    )


def test_validate_backtest_report(tmp_path):
    path = tmp_path / "bt.json"
    _good_report(path)
    data = validate_backtest_report(path)
    assert data["metrics"]["n_taken"] == 3


def test_promote_to_live_requires_backtest_report(tmp_path):
    """No report on disk -> hard gate fails, no promotion."""
    git, ov = _mini_registry(tmp_path, stage="research")
    with pytest.raises(PermissionError):
        promote_to_live("x", git_path=str(git), override_path=str(ov))


def test_promote_to_live_with_passing_report(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    _good_report(tmp_path / "bt.json")
    result = promote_to_live("x", git_path=str(git), override_path=str(ov))
    assert result["stage"] == "live"
    reg = load_registry(str(git), str(ov))
    assert reg["x"].stage == "live"
    assert reg["x"].mode == "live"


def test_promote_to_live_blocked_when_expectancy_negative(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    _good_report(tmp_path / "bt.json", expectancy=-0.5)
    with pytest.raises(PermissionError):
        promote_to_live("x", git_path=str(git), override_path=str(ov))


def test_promote_to_live_blocked_when_drawdown_exceeds_gate(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    _good_report(tmp_path / "bt.json", max_drawdown=0.9)
    with pytest.raises(PermissionError):
        promote_to_live("x", git_path=str(git), override_path=str(ov))


def test_promote_to_live_force_requires_reason(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    _good_report(tmp_path / "bt.json", n_taken=0, expectancy=-1.0)
    with pytest.raises(PermissionError):
        promote_to_live("x", git_path=str(git), override_path=str(ov), force=True)
    result = promote_to_live(
        "x",
        git_path=str(git),
        override_path=str(ov),
        force=True,
        reason="manual override for test",
    )
    assert result["stage"] == "live"
    assert result["history"]["forced"] is True


def test_demote_live_to_research(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="live", mode="live")
    result = demote("x", to_stage="research", git_path=str(git), override_path=str(ov))
    assert result["stage"] == "research"
    reg = load_registry(str(git), str(ov))
    assert reg["x"].stage == "research"
    assert reg["x"].mode == "backtest"


def test_demote_rejects_paper_stage(tmp_path):
    """The paper stage no longer exists."""
    git, ov = _mini_registry(tmp_path, stage="live", mode="live")
    with pytest.raises(ValueError):
        demote("x", to_stage="paper", git_path=str(git), override_path=str(ov))


def test_check_promotion_reports_structure(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    _good_report(tmp_path / "bt.json")
    reg = load_registry(str(git), str(ov))
    check = check_promotion(reg["x"])
    assert check["soft"]["passed"] is True
    assert check["hard"]["stage_is_research"] is True
    assert check["hard"]["backtest_report_valid"] is True
    assert check["passed"] is True
