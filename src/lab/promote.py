"""Promotion gates and history (research → paper → live)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from src.lab.ledger import load_state
from src.lab.metrics.gates import evaluate_soft_gates
from src.lab.registry import Experiment, load_registry, resolve_ledger_path


BACKTEST_REPORT_REQUIRED_KEYS = ("strategy", "window", "metrics")


def validate_backtest_report(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"backtest report missing: {path}")
    data = json.loads(p.read_text())
    for k in BACKTEST_REPORT_REQUIRED_KEYS:
        if k not in data:
            raise ValueError(f"backtest report missing key {k!r}")
    metrics = data["metrics"]
    if int(metrics.get("n_taken", 0)) < 1:
        raise ValueError("backtest report metrics.n_taken must be >= 1")
    return data


def write_backtest_report(path: str | Path, report: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2) + "\n")


def check_promotion(
    exp: Experiment,
    *,
    ledger_path: Optional[str] = None,
) -> dict[str, Any]:
    """Soft + structural checks for paper→live. Does not mutate registry."""
    path = ledger_path or resolve_ledger_path(exp)
    state = load_state(path, starting_equity=exp.capital)
    soft = evaluate_soft_gates(state, exp.gates or {})
    hard = {
        "stage_is_paper": exp.stage == "paper",
        "mode_is_paper_or_live": exp.mode in ("paper", "live"),
    }
    hard_ok = all(hard.values())
    return {
        "experiment_id": exp.id,
        "ledger_path": path,
        "hard": hard,
        "hard_passed": hard_ok,
        "soft": soft,
        "passed": hard_ok and soft["passed"],
    }


def _history_entry(
    *,
    action: str,
    from_stage: str,
    to_stage: str,
    forced: bool,
    reason: Optional[str],
    evidence: Optional[str],
    by: str = "operator",
) -> dict[str, Any]:
    return {
        "action": action,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "forced": forced,
        "force_reason": reason,
        "evidence": evidence,
        "promoted_by": by,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def apply_stage_override(
    exp_id: str,
    *,
    stage: str,
    mode: Optional[str] = None,
    override_path: str = "state/experiments/overrides.yaml",
    history_entry: Optional[dict] = None,
    promotion_extra: Optional[dict] = None,
) -> None:
    """Write/merge experiment stage into overrides.yaml (server-side SoT for stage)."""
    path = Path(override_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"experiments": {}}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {"experiments": {}}
    data.setdefault("experiments", {})
    row = dict(data["experiments"].get(exp_id) or {})
    row["stage"] = stage
    if mode is not None:
        row["mode"] = mode
    promo = dict(row.get("promotion") or {})
    if history_entry:
        hist = list(promo.get("history") or [])
        hist.append(history_entry)
        promo["history"] = hist
        promo["promoted_by"] = history_entry.get("promoted_by")
        promo["at"] = history_entry.get("at")
        promo["forced"] = history_entry.get("forced", False)
        promo["force_reason"] = history_entry.get("force_reason")
        promo["evidence"] = history_entry.get("evidence")
    if promotion_extra:
        promo.update(promotion_extra)
    row["promotion"] = promo
    data["experiments"][exp_id] = row
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def promote_to_paper(
    exp_id: str,
    *,
    git_path: str = "config/experiments.yaml",
    override_path: str = "state/experiments/overrides.yaml",
    backtest_report: Optional[str] = None,
    force: bool = False,
    reason: Optional[str] = None,
    by: str = "operator",
) -> dict[str, Any]:
    reg = load_registry(git_path, override_path)
    exp = reg[exp_id]
    if exp.stage not in ("research", "paper"):
        raise PermissionError(f"promote to paper from stage={exp.stage} not allowed")
    if exp.stage == "paper":
        return {"ok": True, "noop": True, "stage": "paper"}

    report_path = backtest_report or exp.backtest_report_path
    if not force:
        if not report_path:
            raise PermissionError("research→paper requires --backtest-report or exp.backtest_report_path")
        try:
            validate_backtest_report(report_path)
        except (FileNotFoundError, ValueError) as e:
            raise PermissionError(str(e)) from e
    elif report_path and Path(report_path).exists():
        try:
            validate_backtest_report(report_path)
        except (ValueError, FileNotFoundError):
            pass

    entry = _history_entry(
        action="promote_to_paper",
        from_stage=exp.stage,
        to_stage="paper",
        forced=force,
        reason=reason,
        evidence=report_path,
        by=by,
    )
    apply_stage_override(
        exp_id,
        stage="paper",
        mode="paper",
        override_path=override_path,
        history_entry=entry,
    )
    return {"ok": True, "stage": "paper", "history": entry}


def promote_to_live(
    exp_id: str,
    *,
    git_path: str = "config/experiments.yaml",
    override_path: str = "state/experiments/overrides.yaml",
    force: bool = False,
    reason: Optional[str] = None,
    by: str = "operator",
    enable_orb_live: bool = False,
) -> dict[str, Any]:
    reg = load_registry(git_path, override_path)
    exp = reg[exp_id]
    others = [e.id for e in reg.values() if e.id != exp_id and e.stage == "live"]
    if others:
        raise PermissionError(f"another stage=live experiment active: {others}")
    if enable_orb_live:
        raise PermissionError("ENABLE_ORB_LIVE must be false for lab live promotion")

    check = check_promotion(exp)
    check_path = Path(f"state/experiments/{exp_id}/promote_check.json")
    check_path.parent.mkdir(parents=True, exist_ok=True)
    check_path.write_text(json.dumps(check, indent=2) + "\n")

    if exp.stage != "paper" and not force:
        raise PermissionError(f"promote to live requires stage=paper (got {exp.stage})")

    if not check["passed"] and not force:
        raise PermissionError(
            f"soft/hard promotion checks failed; re-run with --force --reason or fix metrics. "
            f"See {check_path}"
        )
    if not check["passed"] and force and not reason:
        raise PermissionError("--force requires --reason when soft gates fail")

    entry = _history_entry(
        action="promote_to_live",
        from_stage=exp.stage,
        to_stage="live",
        forced=force and not check["passed"],
        reason=reason,
        evidence=str(check_path),
        by=by,
    )
    apply_stage_override(
        exp_id,
        stage="live",
        mode="live",
        override_path=override_path,
        history_entry=entry,
    )
    return {"ok": True, "stage": "live", "check": check, "history": entry}


def demote(
    exp_id: str,
    *,
    to_stage: str = "paper",
    git_path: str = "config/experiments.yaml",
    override_path: str = "state/experiments/overrides.yaml",
    reason: Optional[str] = None,
    by: str = "operator",
) -> dict[str, Any]:
    if to_stage not in ("paper", "research"):
        raise ValueError("demote target must be paper or research")
    reg = load_registry(git_path, override_path)
    exp = reg[exp_id]
    entry = _history_entry(
        action="demote",
        from_stage=exp.stage,
        to_stage=to_stage,
        forced=False,
        reason=reason,
        evidence=None,
        by=by,
    )
    mode = "paper" if to_stage in ("paper", "research") else exp.mode
    apply_stage_override(
        exp_id,
        stage=to_stage,
        mode=mode,
        override_path=override_path,
        history_entry=entry,
    )
    return {"ok": True, "stage": to_stage, "history": entry}
