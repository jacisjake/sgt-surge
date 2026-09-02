"""The promote gate must bind the path that actually spends money.

live_swing places real fractional orders guarded only by TRADING_MODE. Without
this check a stage=research experiment with no backtest report still trades
real money, which makes the whole research -> live gate decorative.
"""
from __future__ import annotations

import yaml

from scripts.live_swing import check_live_gate


def _reg(tmp_path, stage, mode="live"):
    p = tmp_path / "exp.yaml"
    p.write_text(yaml.dump({
        "version": 1,
        "experiments": {"x": {
            "strategy": "breakout_52w", "params": {}, "capital": 200.0,
            "mode": mode, "stage": stage,
            "symbols_file": str(tmp_path / "u.txt"),
            "ledger_path": str(tmp_path / "l.json"),
        }},
    }))
    (tmp_path / "u.txt").write_text("AAA\n")
    return str(p), str(tmp_path / "ov.yaml")


def test_gate_refuses_a_research_stage_experiment(tmp_path):
    git, ov = _reg(tmp_path, "research")
    reason = check_live_gate("x", trading_mode="live", enable_orb_live=False,
                             git_path=git, override_path=ov)
    assert reason is not None
    assert "stage" in reason.lower()


def test_gate_permits_a_promoted_experiment(tmp_path):
    git, ov = _reg(tmp_path, "live")
    assert check_live_gate("x", trading_mode="live", enable_orb_live=False,
                           git_path=git, override_path=ov) is None


def test_gate_refuses_when_orb_live_is_enabled(tmp_path):
    """Two live order paths at once is never allowed."""
    git, ov = _reg(tmp_path, "live")
    reason = check_live_gate("x", trading_mode="live", enable_orb_live=True,
                             git_path=git, override_path=ov)
    assert reason is not None
    assert "orb" in reason.lower()


def test_gate_refuses_when_trading_mode_is_not_live(tmp_path):
    git, ov = _reg(tmp_path, "live")
    reason = check_live_gate("x", trading_mode="dry_run", enable_orb_live=False,
                             git_path=git, override_path=ov)
    assert reason is not None


def test_gate_refuses_an_unknown_experiment(tmp_path):
    git, ov = _reg(tmp_path, "live")
    reason = check_live_gate("nope", trading_mode="live", enable_orb_live=False,
                             git_path=git, override_path=ov)
    assert "unknown experiment" in reason.lower()


def test_gate_refuses_when_the_registry_is_unreadable(tmp_path):
    """A missing registry must fail closed, never open."""
    reason = check_live_gate("x", trading_mode="live", enable_orb_live=False,
                             git_path=str(tmp_path / "missing.yaml"),
                             override_path=str(tmp_path / "ov.yaml"))
    assert reason is not None
    assert "registry" in reason.lower()


def test_gate_refuses_the_real_registry_today(tmp_path):
    """breakout_52w_live is stage=research with no report — must refuse."""
    reason = check_live_gate("breakout_52w_live", trading_mode="live",
                             enable_orb_live=False,
                             git_path="config/experiments.yaml",
                             override_path=str(tmp_path / "none.yaml"))
    assert reason is not None


# ── a refused gate must still let positions exit ───────────────────────────

def test_gate_refusal_still_permits_exits():
    """Blocking new risk must never trap an open position without its stop."""
    from scripts.live_swing import apply_gate_to_plan

    plan = [
        {"action": "sell", "symbol": "FRSH", "qty": 1.8, "reason": "stop"},
        {"action": "buy", "symbol": "NEW", "qty": 2.0, "reason": "fresh_breakout"},
    ]
    allowed, blocked = apply_gate_to_plan(plan, gated=True)
    assert [o["symbol"] for o in allowed] == ["FRSH"]
    assert [o["symbol"] for o in blocked] == ["NEW"]


def test_ungated_plan_passes_through_untouched():
    from scripts.live_swing import apply_gate_to_plan

    plan = [{"action": "buy", "symbol": "NEW", "qty": 2.0, "reason": "fresh_breakout"}]
    allowed, blocked = apply_gate_to_plan(plan, gated=False)
    assert allowed == plan
    assert blocked == []


def test_gated_plan_with_only_buys_allows_nothing():
    from scripts.live_swing import apply_gate_to_plan

    plan = [{"action": "buy", "symbol": "A", "qty": 1.0, "reason": "fresh_breakout"}]
    allowed, blocked = apply_gate_to_plan(plan, gated=True)
    assert allowed == []
    assert len(blocked) == 1
