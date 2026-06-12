import pandas as pd
from scripts.research.gapper_universe import qualifies, rank_day, DEFAULT_PARAMS

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
