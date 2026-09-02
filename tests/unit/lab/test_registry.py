"""Registry load + hard gates."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lab.registry import assert_can_run, load_registry, resolve_ledger_path


def test_load_default_experiments_yaml():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/overrides.yaml")
    assert "breakout_52w_live" in reg
    exp = reg["breakout_52w_live"]
    assert exp.strategy == "breakout_52w"
    assert exp.mode == "live"
    assert exp.stage == "research"
    assert exp.params["lookback"] == 252


def test_no_paper_experiments_remain():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/overrides.yaml")
    assert all(e.stage != "paper" for e in reg.values())
    assert all(e.mode != "paper" for e in reg.values())


def test_resolve_returns_ledger_path():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["breakout_52w_live"]
    assert resolve_ledger_path(exp) == exp.ledger_path


def test_assert_can_run_dry_run_ok_at_any_stage():
    """dry_run never reaches the broker, so it is allowed everywhere."""
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    for exp in reg.values():
        assert_can_run(reg, exp, "dry_run")


def test_assert_can_run_live_blocked_for_research_stage():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["breakout_52w_live"]
    with pytest.raises(PermissionError, match="promote to live"):
        assert_can_run(reg, exp, "live", trading_mode="live")


def test_assert_can_run_rejects_paper_mode():
    """'paper' is no longer a mode the lab understands."""
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["short_term_reversal_research"]
    with pytest.raises(PermissionError, match="unknown mode"):
        assert_can_run(reg, exp, "paper")


def test_override_merges(tmp_path):
    base = {
        "version": 1,
        "defaults": {"gates": {"min_closed_trades": 15}},
        "experiments": {
            "breakout_52w_paper": {
                "strategy": "breakout_52w",
                "params": {"lookback": 252},
                "capital": 200.0,
                "mode": "paper",
                "stage": "paper",
                "symbols_file": "state/u.txt",
                "ledger_path": "state/l.json",
            }
        },
    }
    git = tmp_path / "exp.yaml"
    git.write_text(yaml.dump(base))
    ov = tmp_path / "ov.yaml"
    ov.write_text(
        yaml.dump(
            {
                "experiments": {
                    "breakout_52w_paper": {"params": {"lookback": 100}},
                }
            }
        )
    )
    reg = load_registry(str(git), str(ov))
    assert reg["breakout_52w_paper"].params["lookback"] == 100
