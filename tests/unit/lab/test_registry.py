"""Registry load + hard gates."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lab.registry import assert_can_run, load_registry, resolve_ledger_path


def test_load_default_experiments_yaml():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/overrides.yaml")
    assert "breakout_52w_paper" in reg
    exp = reg["breakout_52w_paper"]
    assert exp.strategy == "breakout_52w"
    assert exp.mode == "paper"
    assert exp.stage == "paper"
    assert exp.params["lookback"] == 252
    assert exp.legacy_ledger_path == "state/swing_paper_breakout.json"


def test_resolve_prefers_legacy_when_missing_new(tmp_path):
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["breakout_52w_paper"]
    # neither path may exist in CI — resolve_ledger_path falls back to legacy when set
    path = resolve_ledger_path(exp)
    assert path in (exp.ledger_path, exp.legacy_ledger_path)


def test_assert_can_run_paper_ok():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["breakout_52w_paper"]
    assert_can_run(reg, exp, "paper")


def test_assert_can_run_live_blocked_for_paper_stage():
    reg = load_registry("config/experiments.yaml", override_path="/nonexistent/x.yaml")
    exp = reg["breakout_52w_paper"]
    with pytest.raises(PermissionError):
        assert_can_run(reg, exp, "live", trading_mode="live")


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
