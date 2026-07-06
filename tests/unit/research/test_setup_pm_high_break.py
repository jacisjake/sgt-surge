from scripts.research.indicators_ctx import build_context
from scripts.research.setups.pm_high_break import PMHighBreak
from tests.unit.research.fixtures import make_day

def test_pm_high_break_enters_on_close_above_pm_high():
    # fixture PM bar high = 8.2; a session bar closes above it -> entry
    day = make_day(session_closes=[7.9, 8.0, 8.3, 8.6, 8.5, 8.7],
                   session_highs=[8.0, 8.1, 8.4, 8.7, 8.6, 8.8],
                   session_lows=[7.7, 7.9, 8.0, 8.3, 8.3, 8.4])
    ctx = build_context(day)
    trade = PMHighBreak().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "pm_high_break"

def test_pm_high_break_returns_none_without_pm_data():
    day = make_day(session_closes=[8.3, 8.4, 8.5, 8.6, 8.5, 8.7],
                   session_highs=[8.4, 8.5, 8.6, 8.7, 8.6, 8.8],
                   session_lows=[8.0, 8.1, 8.2, 8.3, 8.3, 8.4], pm=False)
    ctx = build_context(day)
    assert PMHighBreak().evaluate(ctx, slip_bps=0.0) is None
