"""Screening predicate for the lab universe (price band + liquidity + history)."""
from src.lab.universe import (
    DEFAULT_PARAMS,
    median_dollar_volume,
    parse_symbols,
    qualifies,
    screen,
    union_symbol_lists,
)



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


def test_fractional_ok_name_clears_price_ceiling():
    row = _row(symbol="NVDA", last_close=180.0)
    assert qualifies(row, DEFAULT_PARAMS) is True


def test_fractional_ok_name_still_needs_liquidity_and_history():
    thin = _row(symbol="MU", last_close=180.0, median_dollar_vol=1.0)
    short = _row(symbol="NVDA", last_close=180.0, n_bars=10)
    assert qualifies(thin, DEFAULT_PARAMS) is False
    assert qualifies(short, DEFAULT_PARAMS) is False


def test_non_allowlisted_mega_cap_still_rejected_above_ceiling():
    assert qualifies(_row(symbol="JPM", last_close=348.0), DEFAULT_PARAMS) is False


def test_screen_includes_fractional_ok_names():
    cheap = _row(symbol="AAA")
    nvda = _row(symbol="NVDA", last_close=180.0)
    jpm = _row(symbol="JPM", last_close=348.0)
    assert screen([cheap, nvda, jpm], DEFAULT_PARAMS) == ["AAA", "NVDA"]


def test_parse_symbols_splits_and_dedupes():
    assert parse_symbols("nvda MU\nAMAT amat") == ["AMAT", "MU", "NVDA"]


def test_union_keeps_cheap_screen_and_overlay():
    cheap = ["AAL", "AES", "F"]
    overlay = ["NVDA", "MU", "AMAT"]
    merged = union_symbol_lists(cheap, overlay)
    assert "AAL" in merged and "NVDA" in merged and "AMAT" in merged
    assert merged == sorted(set(cheap) | set(overlay))


