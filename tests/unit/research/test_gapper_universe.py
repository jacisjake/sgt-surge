import datetime
import pandas as pd
import pytz
from scripts.research.gapper_universe import qualifies, rank_day, DEFAULT_PARAMS, compute_levels

def _daily(prev_close, open_, close, volume):
    return {"prev_close": prev_close, "open": open_, "close": close, "volume": volume}

def test_qualifies_true_for_clean_gapper():
    # 25% gap, $5 price, $4M dollar-vol
    assert qualifies(_daily(4.0, 5.0, 5.2, 800_000), DEFAULT_PARAMS) is True

def test_qualifies_false_small_gap():
    assert qualifies(_daily(4.9, 5.0, 5.1, 800_000), DEFAULT_PARAMS) is False

def test_qualifies_false_too_expensive():
    assert qualifies(_daily(20.0, 25.0, 25.0, 800_000), DEFAULT_PARAMS) is False

def test_qualifies_false_thin_volume():
    assert qualifies(_daily(4.0, 5.0, 5.0, 1000), DEFAULT_PARAMS) is False

def test_rank_day_takes_top_n_by_gap():
    rows = {
        "AAA": _daily(4.0, 5.0, 5.0, 800_000),    # +25%
        "BBB": _daily(2.0, 3.0, 3.0, 2_000_000),  # +50%
        "CCC": _daily(4.9, 5.0, 5.0, 800_000),    # +2% (excluded)
    }
    out = rank_day(rows, {**DEFAULT_PARAMS, "top_n": 1})
    assert out == ["BBB"]


def _make_daily_df(rows):
    """Build a tz-aware daily DataFrame. rows = list of (date_str, open, high, low, close, volume)."""
    ET = pytz.timezone("America/New_York")
    idx = pd.DatetimeIndex(
        [ET.localize(datetime.datetime.fromisoformat(r[0])) for r in rows]
    )
    return pd.DataFrame(
        {"open": [r[1] for r in rows],
         "high": [r[2] for r in rows],
         "low":  [r[3] for r in rows],
         "close":[r[4] for r in rows],
         "volume":[r[5] for r in rows]},
        index=idx,
    )


def test_compute_levels_prev_and_swing():
    # 12 rows: swing window = rows 0..9 (10 rows), prior day = row 10, target day = row 11
    rows = [
        ("2026-01-02", 5.0, 6.0, 4.0, 5.5, 100_000),  # idx 0  swing
        ("2026-01-05", 5.1, 7.5, 4.5, 5.6, 100_000),  # idx 1  swing high candidate
        ("2026-01-06", 5.2, 6.2, 3.5, 5.3, 100_000),  # idx 2  swing low candidate
        ("2026-01-07", 5.0, 6.1, 4.1, 5.0, 100_000),  # idx 3
        ("2026-01-08", 5.1, 6.3, 4.2, 5.2, 100_000),  # idx 4
        ("2026-01-09", 5.0, 6.0, 4.0, 5.1, 100_000),  # idx 5
        ("2026-01-12", 5.0, 6.1, 4.1, 5.0, 100_000),  # idx 6
        ("2026-01-13", 5.2, 6.2, 4.2, 5.2, 100_000),  # idx 7
        ("2026-01-14", 5.1, 6.1, 4.1, 5.1, 100_000),  # idx 8
        ("2026-01-15", 5.0, 6.0, 4.0, 5.0, 100_000),  # idx 9  (last of swing window)
        ("2026-01-16", 5.5, 8.0, 3.0, 5.8, 100_000),  # idx 10 prior day
        ("2026-01-20", 6.0, 9.0, 5.0, 7.0, 200_000),  # idx 11 target day
    ]
    df = _make_daily_df(rows)
    target_day = datetime.date(2026, 1, 20)
    lvl = compute_levels(df, target_day, swing_lookback=10)
    assert lvl is not None
    # prior day (row 10): high=8.0, low=3.0
    assert lvl["prev_high"] == 8.0
    assert lvl["prev_low"] == 3.0
    # swing window = rows 0..9 -> max high = 7.5 (row 1), min low = 3.5 (row 2)
    assert lvl["swing_high"] == 7.5
    assert lvl["swing_low"] == 3.5


def test_compute_levels_no_prior_day_returns_none():
    # Only one row — no prior day exists
    rows = [("2026-01-02", 5.0, 6.0, 4.0, 5.5, 100_000)]
    df = _make_daily_df(rows)
    assert compute_levels(df, datetime.date(2026, 1, 2)) is None


def test_compute_levels_swing_fallback_to_prev():
    # prior day exists but no rows before it -> swing falls back to prev_high/low
    rows = [
        ("2026-01-02", 5.0, 8.0, 3.0, 5.5, 100_000),  # prior day
        ("2026-01-05", 6.0, 9.0, 5.0, 7.0, 200_000),  # target day
    ]
    df = _make_daily_df(rows)
    lvl = compute_levels(df, datetime.date(2026, 1, 5))
    assert lvl["swing_high"] == 8.0  # fallback to prev_high
    assert lvl["swing_low"] == 3.0   # fallback to prev_low
