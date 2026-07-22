"""Day-step BacktestRunner parity with paper step."""
from __future__ import annotations

import copy

import pandas as pd

from scripts.research.swing.paper_forward import new_state, step
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


def test_backtest_matches_step_on_single_breakout_day():
    lookback = 10
    df = _make_breakout_bars(lookback)
    today = df.index[-1].date()
    params = {
        "lookback": lookback,
        "ma_exit": 3,
        "stop_pct": 0.08,
        "risk_pct": 0.01,
        "slip_bps": 15.0,
        "use_regime_gate": False,
    }
    s = step(new_state(200.0), {"SYM": df}, today, **{k: params[k] for k in
             ("lookback", "ma_exit", "stop_pct", "risk_pct", "slip_bps")})
    result = run_day_step_backtest(
        "breakout_52w",
        {"SYM": df},
        params,
        capital=200.0,
    )
    st = result["state"]
    assert len(st["open_positions"]) == len(s["open_positions"])
    if st["open_positions"]:
        assert abs(st["open_positions"][0]["notional"] - s["open_positions"][0]["notional"]) < 0.01
        assert abs(st["available_cash"] - s["available_cash"]) < 0.01
    assert result["metrics"]["engine"] == "day_step"
