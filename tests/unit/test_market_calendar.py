from datetime import date

from src.core.market_calendar import NYSE_HOLIDAYS, is_market_open_day


def test_holidays_includes_known_2026_dates():
    # New Year's Day 2026 falls on Thursday — confirmed market closed
    assert date(2026, 1, 1) in NYSE_HOLIDAYS
    # July 4 2026 is a Saturday — observed Friday July 3
    assert date(2026, 7, 3) in NYSE_HOLIDAYS


def test_is_market_open_day_excludes_weekends():
    assert not is_market_open_day(date(2026, 5, 9))   # Saturday
    assert not is_market_open_day(date(2026, 5, 10))  # Sunday
    assert is_market_open_day(date(2026, 5, 11))      # Monday


def test_is_market_open_day_excludes_holidays():
    assert not is_market_open_day(date(2026, 1, 1))
