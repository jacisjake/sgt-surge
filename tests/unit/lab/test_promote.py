"""Promotion soft/hard gates and overrides."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.lab.ledger import new_state, save_state
from src.lab.promote import (
    check_promotion,
    demote,
    promote_to_live,
    promote_to_paper,
    validate_backtest_report,
    write_backtest_report,
)
from src.lab.registry import load_registry


def _mini_registry(tmp_path: Path, stage="research", mode="paper"):
    git = tmp_path / "exp.yaml"
    git.write_text(
        yaml.dump(
            {
                "version": 1,
                "defaults": {
                    "gates": {
                        "min_paper_trading_days": 2,
                        "min_closed_trades": 1,
                        "max_paper_drawdown": 0.5,
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


def test_validate_backtest_report(tmp_path):
    path = tmp_path / "bt.json"
    write_backtest_report(
        path,
        {
            "strategy": "short_term_reversal",
            "params": {},
            "window": {"start": "2024-01-01", "end": "2024-06-01"},
            "metrics": {"n_taken": 3, "expectancy": 0.1, "engine": "day_step"},
        },
    )
    data = validate_backtest_report(path)
    assert data["metrics"]["n_taken"] == 3


def test_promote_research_to_paper_requires_report(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    with pytest.raises(PermissionError):
        promote_to_paper("x", git_path=str(git), override_path=str(ov))


def test_promote_research_to_paper_with_report(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="research")
    bt = tmp_path / "bt.json"
    write_backtest_report(
        bt,
        {
            "strategy": "breakout_52w",
            "window": {"start": "a", "end": "b"},
            "metrics": {"n_taken": 1},
        },
    )
    result = promote_to_paper(
        "x", git_path=str(git), override_path=str(ov), backtest_report=str(bt)
    )
    assert result["stage"] == "paper"
    reg = load_registry(str(git), str(ov))
    assert reg["x"].stage == "paper"


def test_promote_to_live_force_with_reason(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="paper", mode="paper")
    # empty ledger fails soft gates
    save_state(str(tmp_path / "ledger.json"), new_state(200.0))
    with pytest.raises(PermissionError):
        promote_to_live("x", git_path=str(git), override_path=str(ov))
    result = promote_to_live(
        "x",
        git_path=str(git),
        override_path=str(ov),
        force=True,
        reason="manual override for test",
    )
    assert result["stage"] == "live"
    reg = load_registry(str(git), str(ov))
    assert reg["x"].stage == "live"
    assert reg["x"].mode == "live"


def test_demote_live_to_paper(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="live", mode="live")
    result = demote("x", to_stage="paper", git_path=str(git), override_path=str(ov))
    assert result["stage"] == "paper"
    reg = load_registry(str(git), str(ov))
    assert reg["x"].stage == "paper"


def test_check_promotion_writes_structure(tmp_path):
    git, ov = _mini_registry(tmp_path, stage="paper")
    state = new_state(200.0)
    state["closed_trades"] = [
        {"pnl": 1.0, "symbol": "A", "entry_date": "2024-01-01", "exit_date": "2024-01-02",
         "entry_price": 1, "exit_price": 2, "reason": "time"}
    ]
    state["equity_curve_daily"] = [
        {"date": "2024-01-01", "equity_realized": 200.0, "daily_return": 0, "open_positions": 0, "cash": 200},
        {"date": "2024-01-02", "equity_realized": 201.0, "daily_return": 0.005, "open_positions": 0, "cash": 201},
    ]
    save_state(str(tmp_path / "ledger.json"), state)
    reg = load_registry(str(git), str(ov))
    check = check_promotion(reg["x"])
    assert check["soft"]["passed"] is True
