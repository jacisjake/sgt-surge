"""LiveRunner hard gates + idempotency + reject non-fatal."""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from src.lab.fills.broker import execute_plan, intents_to_live_plan
from src.lab.protocol import OrderIntent, Side
from src.lab.registry import assert_can_run, load_registry
from src.lab.runners.live import run_live_day


def _reg(tmp_path: Path, stage="live", mode="live"):
    git = tmp_path / "exp.yaml"
    git.write_text(
        yaml.dump(
            {
                "version": 1,
                "defaults": {"gates": {}},
                "experiments": {
                    "live_x": {
                        "strategy": "breakout_52w",
                        "params": {
                            "lookback": 5,
                            "ma_exit": 3,
                            "stop_pct": 0.08,
                            "risk_pct": 0.01,
                            "use_regime_gate": False,
                        },
                        "capital": 200.0,
                        "mode": mode,
                        "stage": stage,
                        "symbols_file": str(tmp_path / "u.txt"),
                        "ledger_path": str(tmp_path / "l.json"),
                        "live_audit_path": str(tmp_path / "audit.json"),
                    }
                },
            }
        )
    )
    (tmp_path / "u.txt").write_text("AAA\n")
    return load_registry(str(git), str(tmp_path / "ov.yaml"))


def test_assert_can_run_live_requires_stage():
    # use real git yaml breakout paper
    reg = load_registry("config/experiments.yaml", "/nonexistent")
    exp = reg["breakout_52w_paper"]
    with pytest.raises(PermissionError):
        assert_can_run(reg, exp, "live", "live")


def test_assert_can_run_research_denies_paper():
    reg = load_registry("config/experiments.yaml", "/nonexistent")
    exp = reg["short_term_reversal_research"]
    with pytest.raises(PermissionError, match="promote to paper"):
        assert_can_run(reg, exp, "paper")


def test_execute_plan_reject_non_fatal():
    class Boom:
        def execute_market_order(self, *a, **k):
            raise RuntimeError("nope")

    results = execute_plan(
        [{"action": "buy", "symbol": "AAA", "qty": 1.0, "price": 10, "notional": 10, "reason": "x"}],
        Boom(),
    )
    assert results[0]["status"] == "rejected"
    assert "nope" in results[0]["error"]


def test_live_preview_no_submit(tmp_path):
    reg = _reg(tmp_path, stage="live", mode="live")
    exp = reg["live_x"]
    # minimal bars for breakout
    n = 6
    df = pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [11] * (n - 1) + [20.0],
            "low": [9.0] * n,
            "close": [10.0] * (n - 1) + [20.0],
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )
    client = MagicMock()
    client.get_history.side_effect = lambda sym, *a, **k: df if sym == "AAA" else pd.DataFrame()
    client.get_positions.return_value = []
    client.get_account.return_value = {"equity": 200.0, "buying_power": 200.0}
    executor = MagicMock()
    out = run_live_day(
        exp, client, executor, reg, preview=True, trading_mode="live"
    )
    assert out["preview"] is True
    executor.execute_market_order.assert_not_called()


def test_live_idempotency_skips_second_submit(tmp_path):
    reg = _reg(tmp_path, stage="live", mode="live")
    exp = reg["live_x"]
    n = 6
    df = pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [11] * (n - 1) + [20.0],
            "low": [9.0] * n,
            "close": [10.0] * (n - 1) + [20.0],
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )
    client = MagicMock()
    client.get_history.side_effect = lambda sym, *a, **k: df if sym == "AAA" else pd.DataFrame()
    client.get_positions.return_value = []
    client.get_account.return_value = {"equity": 200.0, "buying_power": 200.0}

    class OkExec:
        def execute_market_order(self, *a, **k):
            return "oid"

    # first run
    out1 = run_live_day(
        exp, client, OkExec(), reg, preview=False, trading_mode="live"
    )
    assert out1.get("skipped") is not True
    # second run same day
    out2 = run_live_day(
        exp, client, OkExec(), reg, preview=False, trading_mode="live"
    )
    assert out2.get("skipped") is True
