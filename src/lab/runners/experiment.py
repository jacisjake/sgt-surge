"""ExperimentRunner — dispatch by experiment id / mode."""
from __future__ import annotations

from datetime import date
from typing import Any

from src.lab.registry import Experiment, assert_can_run, load_registry
from src.lab.runners.backtest import run_day_step_backtest, write_report_from_backtest
from src.lab.runners.live import run_live_day
from src.lab.runners.paper import run_paper_day


class ExperimentRunner:
    def __init__(
        self,
        registry: dict[str, Experiment] | None = None,
        *,
        git_path: str = "config/experiments.yaml",
        override_path: str = "state/experiments/overrides.yaml",
    ):
        self.git_path = git_path
        self.override_path = override_path
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
        enable_orb_live: bool = False,
        executor=None,
        bars_by_symbol: dict | None = None,
        spy_df=None,
        write_report: bool = False,
    ) -> dict[str, Any]:
        exp = self.registry[experiment_id]
        mode = mode or exp.mode
        assert_can_run(
            self.registry,
            exp,
            mode,
            trading_mode,
            preview=preview,
            enable_orb_live=enable_orb_live,
        )

        if mode == "backtest":
            if bars_by_symbol is None:
                raise ValueError("backtest mode requires bars_by_symbol")
            result = run_day_step_backtest(
                exp.strategy,
                bars_by_symbol,
                exp.params,
                capital=exp.capital,
                spy_df=spy_df,
            )
            if write_report and exp.backtest_report_path:
                write_report_from_backtest(
                    exp.backtest_report_path,
                    strategy=exp.strategy,
                    params=exp.params,
                    result=result,
                )
            return {"ok": True, "mode": "backtest", **result["metrics"], "window": result["window"]}

        if mode in ("paper",):
            return run_paper_day(exp, client, as_of=as_of)

        if mode == "live":
            if executor is None and not preview:
                raise ValueError("live mode requires executor unless preview=True")
            return run_live_day(
                exp,
                client,
                executor,
                self.registry,
                as_of=as_of,
                preview=preview,
                trading_mode=trading_mode or "live",
                enable_orb_live=enable_orb_live,
            )

        raise ValueError(f"unsupported mode {mode!r}")
