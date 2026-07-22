"""ExperimentRunner — dispatch by experiment id / mode."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.lab.registry import Experiment, assert_can_run, load_registry
from src.lab.runners.paper import run_paper_day


class ExperimentRunner:
    def __init__(
        self,
        registry: dict[str, Experiment] | None = None,
        *,
        git_path: str = "config/experiments.yaml",
        override_path: str = "state/experiments/overrides.yaml",
    ):
        self.registry = registry or load_registry(git_path, override_path)

    def run(
        self,
        experiment_id: str,
        client,
        *,
        as_of: date | None = None,
        mode: str | None = None,
        preview: bool = False,
        trading_mode: str | None = None,
    ) -> dict[str, Any]:
        exp = self.registry[experiment_id]
        mode = mode or exp.mode
        assert_can_run(self.registry, exp, mode, trading_mode, preview=preview)

        if preview:
            # Lightweight: paper path but caller can inspect intents from result
            pass

        if mode in ("paper", "research") or exp.mode == "paper":
            if mode == "live":
                raise PermissionError("use LiveRunner for live (not in core batch)")
            return run_paper_day(exp, client, as_of=as_of)

        raise ValueError(f"unsupported mode {mode!r} in core batch (paper only)")
