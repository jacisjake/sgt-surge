import pandas as pd
from scripts.research.run_harness import run_setups_on_day, ALL_SETUPS

def _day():
    times = ["13:25", "13:30", "13:35", "13:40", "13:45", "13:50", "13:55", "14:00"]
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in times])
    return pd.DataFrame({
        "open":   [8.0, 9.0, 9.4, 9.2, 9.9, 10.2, 10.4, 10.3],
        "high":   [8.2, 9.5, 9.6, 9.5, 10.0, 10.3, 10.5, 10.4],
        "low":    [7.9, 8.9, 9.1, 9.0, 9.6, 10.0, 10.1, 10.0],
        "close":  [8.1, 9.4, 9.2, 9.4, 9.9, 10.2, 10.4, 10.3],
        "volume": [500, 4000, 3000, 2000, 1500, 1200, 1100, 1000],
    }, index=idx)

def test_run_setups_on_day_returns_trades_keyed_by_setup():
    out = run_setups_on_day("AAA", _day(), slip_bps=0.0)
    assert set(out.keys()) == set(s.key for s in ALL_SETUPS)
    # ORB-clean should have produced a trade on this breakout day
    assert out["orb_clean"] is not None
    assert out["orb_clean"].symbol == "AAA"
