"""Day-step BacktestRunner behaviour on a single fresh breakout."""
from __future__ import annotations

import pandas as pd

from src.lab.runners.backtest import run_day_step_backtest


def _make_breakout_bars(lookback: int = 10) -> pd.DataFrame:
    n = lookback + 1
    rows = []
    for i in range(n):
        if i == lookback - 1:
            c, h = 99.0, 99.0
        elif i == lookback:
            c, h = 101.0, 102.0
        else:
            c, h = 100.0, 100.0
        rows.append(
            {"open": c - 0.1, "high": h, "low": c - 0.5, "close": c, "volume": 1_000_000}
        )
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-02", periods=n, freq="B", tz="America/New_York")
    return df


BASE_PARAMS = {
    "lookback": 10,
    "ma_exit": 3,
    "stop_pct": 0.08,
    "risk_pct": 0.01,
    "slip_bps": 15.0,
    "use_regime_gate": False,
}


def _run(params=None, capital=200.0):
    return run_day_step_backtest(
        "breakout_52w",
        {"SYM": _make_breakout_bars()},
        {**BASE_PARAMS, **(params or {})},
        capital=capital,
    )


def test_fresh_breakout_opens_one_position():
    st = _run()["state"]
    assert len(st["open_positions"]) == 1
    pos = st["open_positions"][0]
    assert pos["symbol"] == "SYM"
    assert pos["entry_price"] == 101.0
    # stop_pct 0.08 below entry
    assert abs(pos["stop_price"] - 101.0 * 0.92) < 0.01


def test_position_sized_by_risk_budget():
    """notional = capital * risk_pct / stop_pct -> 200 * 0.01 / 0.08 = 25."""
    pos = _run()["state"]["open_positions"][0]
    assert abs(pos["notional"] - 25.0) < 0.01


def test_risk_off_override_blocks_the_entry():
    st = _run({"use_regime_gate": True, "risk_on_override": False})["state"]
    assert st["open_positions"] == []


def test_metrics_report_day_step_engine_and_no_closed_trades():
    metrics = _run()["metrics"]
    assert metrics["engine"] == "day_step"
    assert metrics["n_taken"] == 0
    assert metrics["final_equity"] == 200.0
