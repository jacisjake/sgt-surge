"""Experiment registry: git yaml + optional server overrides."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class Experiment:
    id: str
    strategy: str
    params: dict[str, Any]
    capital: float
    mode: str
    stage: str
    symbols_file: str
    ledger_path: str
    legacy_ledger_path: Optional[str] = None
    backtest_report_path: Optional[str] = None
    live_audit_path: Optional[str] = None
    schedule: Optional[str] = None
    crontab_owner: Optional[str] = None
    promotion: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)


def _merge_dict(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def load_registry(
    git_path: str = "config/experiments.yaml",
    override_path: str = "state/experiments/overrides.yaml",
) -> dict[str, Experiment]:
    path = Path(git_path)
    if not path.exists():
        raise FileNotFoundError(f"experiments yaml missing: {git_path}")
    base = yaml.safe_load(path.read_text()) or {}
    defaults = base.get("defaults") or {}
    default_gates = defaults.get("gates") or {}
    experiments: dict[str, Experiment] = {}

    for exp_id, raw in (base.get("experiments") or {}).items():
        merged = _merge_dict({"gates": default_gates}, raw or {})
        experiments[exp_id] = _to_experiment(exp_id, merged)

    ov_path = Path(override_path)
    if ov_path.exists():
        ov = yaml.safe_load(ov_path.read_text()) or {}
        for exp_id, raw in (ov.get("experiments") or {}).items():
            if exp_id not in experiments:
                raise ValueError(f"override for unknown experiment {exp_id}")
            cur = experiments[exp_id]
            cur_dict = _experiment_to_dict(cur)
            experiments[exp_id] = _to_experiment(exp_id, _merge_dict(cur_dict, raw or {}))

    return experiments


def _experiment_to_dict(cur: Experiment) -> dict:
    return {
        "strategy": cur.strategy,
        "params": cur.params,
        "capital": cur.capital,
        "mode": cur.mode,
        "stage": cur.stage,
        "symbols_file": cur.symbols_file,
        "ledger_path": cur.ledger_path,
        "legacy_ledger_path": cur.legacy_ledger_path,
        "backtest_report_path": cur.backtest_report_path,
        "live_audit_path": cur.live_audit_path,
        "schedule": cur.schedule,
        "crontab_owner": cur.crontab_owner,
        "promotion": cur.promotion,
        "gates": cur.gates,
    }


def _to_experiment(exp_id: str, raw: dict) -> Experiment:
    return Experiment(
        id=exp_id,
        strategy=raw["strategy"],
        params=dict(raw.get("params") or {}),
        capital=float(raw.get("capital", 200.0)),
        mode=str(raw.get("mode", "paper")),
        stage=str(raw.get("stage", "research")),
        symbols_file=str(raw["symbols_file"]),
        ledger_path=str(raw["ledger_path"]),
        legacy_ledger_path=raw.get("legacy_ledger_path"),
        backtest_report_path=raw.get("backtest_report_path"),
        live_audit_path=raw.get("live_audit_path"),
        schedule=raw.get("schedule"),
        crontab_owner=raw.get("crontab_owner"),
        promotion=dict(raw.get("promotion") or {}),
        gates=dict(raw.get("gates") or {}),
    )


def resolve_ledger_path(exp: Experiment) -> str:
    """Prefer lab ledger_path; fall back to legacy only if new file missing.

    Migration (PR14): when ledger_path exists, always use it. When only the
    legacy file exists, return legacy so readers still work; writers should call
    ``src.lab.paths.maybe_migrate_legacy`` to copy forward.
    """
    ledger = Path(exp.ledger_path)
    if ledger.exists():
        return exp.ledger_path
    if exp.legacy_ledger_path and Path(exp.legacy_ledger_path).exists():
        return exp.legacy_ledger_path
    return exp.ledger_path


def assert_can_run(
    experiments: dict[str, Experiment],
    exp: Experiment,
    mode: str,
    trading_mode: str | None = None,
    *,
    preview: bool = False,
    enable_orb_live: bool = False,
) -> None:
    """Hard gates per design mode×stage×TRADING_MODE matrix."""
    if preview:
        return

    mode = mode or exp.mode

    # Invalid registry rows (option A)
    if exp.stage == "live" and exp.mode != "live":
        raise PermissionError(
            "stage=live requires mode=live; use a separate paper id for shadow"
        )

    if mode == "paper":
        if exp.stage == "research":
            raise PermissionError("promote to paper first (stage=research)")
        if exp.stage == "live":
            raise PermissionError(
                "stage=live is live-only; run a separate paper experiment for shadow"
            )
        return

    if mode == "dry_run":
        if exp.stage == "research":
            raise PermissionError("stage research cannot dry_run")
        if exp.stage == "live":
            raise PermissionError("use preview or live mode on stage=live experiment")
        return

    if mode == "live":
        if exp.mode != "live":
            raise PermissionError(
                f"exp.mode!=live — cannot force live on mode={exp.mode}"
            )
        if exp.stage != "live":
            raise PermissionError("stage!=live — promote to live first")
        if trading_mode and trading_mode != "live":
            raise PermissionError(f"TRADING_MODE must be live (got {trading_mode})")
        others = [e.id for e in experiments.values() if e.id != exp.id and e.stage == "live"]
        if others:
            raise PermissionError(f"another stage=live experiment active: {others[0]}")
        if enable_orb_live:
            raise PermissionError("ENABLE_ORB_LIVE must be false for lab live")
        return

    if mode == "backtest":
        return

    raise PermissionError(f"unknown mode {mode!r}")
