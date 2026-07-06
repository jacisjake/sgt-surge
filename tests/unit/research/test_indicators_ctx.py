import pandas as pd
from scripts.research.indicators_ctx import build_context

def _day():
    # 09:25 PM bar, then 09:30..10:10 session 5-min bars (ET), UTC index (+4 in EDT)
    times = ["13:25", "13:30", "13:35", "13:40", "13:45", "13:50", "13:55", "14:00"]
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in times])
    data = {
        "open":   [8.0, 9.0, 9.4, 9.2, 9.6, 9.5, 9.8, 9.7],
        "high":   [8.2, 9.5, 9.6, 9.5, 9.9, 9.7, 10.0, 9.9],
        "low":    [7.9, 8.9, 9.1, 9.0, 9.3, 9.4, 9.6, 9.5],
        "close":  [8.1, 9.4, 9.2, 9.4, 9.7, 9.6, 9.9, 9.8],
        "volume": [500, 4000, 3000, 2000, 1500, 1200, 1100, 1000],
    }
    return pd.DataFrame(data, index=idx)

def test_build_context_or_pm_and_columns():
    ctx = build_context(_day())
    # OR window 09:30-09:45 ET = first three session bars (13:30,13:35,13:40 UTC)
    assert ctx.or_high == 9.6
    assert ctx.or_low == 8.9
    assert ctx.or_volume == 9000
    # PM high from the 09:25 ET bar
    assert ctx.pm_high == 8.2
    # session frame excludes PM and carries indicator columns
    assert len(ctx.bars) == 7
    for col in ("et_time", "vwap", "ema9", "atr"):
        assert col in ctx.bars.columns
    # vwap of first session bar = typical price (h+l+c)/3
    first = ctx.bars.iloc[0]
    assert round(first["vwap"], 4) == round((9.5 + 8.9 + 9.4) / 3, 4)


def test_build_context_levels_populated():
    levels = {"prev_high": 10.5, "prev_low": 9.1, "swing_high": 12.0, "swing_low": 8.5}
    ctx = build_context(_day(), levels=levels)
    assert ctx.prev_high == 10.5
    assert ctx.prev_low == 9.1
    assert ctx.swing_high == 12.0
    assert ctx.swing_low == 8.5


def test_build_context_levels_default_none():
    ctx = build_context(_day())
    assert ctx.prev_high is None
    assert ctx.prev_low is None
    assert ctx.swing_high is None
    assert ctx.swing_low is None
