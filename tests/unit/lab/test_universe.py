"""Screening predicate for the lab universe (price band + liquidity + history)."""
from src.lab.universe import DEFAULT_PARAMS, median_dollar_volume, qualifies, screen


def _row(symbol="AAA", last_close=12.0, median_dollar_vol=20_000_000.0, n_bars=300):
    return {
        "symbol": symbol,
        "last_close": last_close,
        "median_dollar_vol": median_dollar_vol,
        "n_bars": n_bars,
    }


def test_accepts_symbol_clearing_every_filter():
    assert qualifies(_row(), DEFAULT_PARAMS) is True


def test_rejects_symbol_priced_above_ceiling():
    row = _row(last_close=DEFAULT_PARAMS["price_max"] + 0.01)
    assert qualifies(row, DEFAULT_PARAMS) is False


def test_rejects_symbol_priced_below_floor():
    row = _row(last_close=DEFAULT_PARAMS["price_min"] - 0.01)
    assert qualifies(row, DEFAULT_PARAMS) is False


def test_rejects_symbol_with_insufficient_history():
    """breakout_52w needs a 252-bar lookback; a short history can never signal."""
    row = _row(n_bars=DEFAULT_PARAMS["min_bars"] - 1)
    assert qualifies(row, DEFAULT_PARAMS) is False


def test_rejects_thin_symbol_whose_mean_volume_is_inflated_by_one_spike():
    """The reason the floor is a median: one spike must not qualify a thin name."""
    closes = [10.0] * 5
    volumes = [1_000.0, 1_000.0, 1_000.0, 1_000.0, 10_000_000.0]
    med = median_dollar_volume(closes, volumes)
    mean = sum(c * v for c, v in zip(closes, volumes)) / len(closes)

    assert mean > DEFAULT_PARAMS["min_dollar_vol"]   # a mean floor would pass it
    assert med < DEFAULT_PARAMS["min_dollar_vol"]    # the median floor rejects it
    assert qualifies(_row(median_dollar_vol=med), DEFAULT_PARAMS) is False


def test_median_dollar_volume_of_empty_series_is_zero():
    assert median_dollar_volume([], []) == 0.0


def test_screen_dedupes_and_is_order_independent():
    a = _row(symbol="AAA")
    b = _row(symbol="BBB")
    junk = _row(symbol="ZZZ", last_close=999.0)

    assert screen([a, b, junk], DEFAULT_PARAMS) == ["AAA", "BBB"]
    assert screen([junk, b, a, dict(a)], DEFAULT_PARAMS) == ["AAA", "BBB"]


def test_price_ceiling_default_allows_a_whole_share_at_current_sizing():
    """Sizing is ~$24/position (risk_pct*equity/stop_frac); the ceiling must fit
    inside that or every fill is fractional again."""
    assert DEFAULT_PARAMS["price_max"] <= 25.0
