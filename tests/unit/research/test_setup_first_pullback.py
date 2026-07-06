from scripts.research.indicators_ctx import build_context
from scripts.research.setups.first_pullback import FirstPullback
from tests.unit.research.fixtures import make_day

def test_first_pullback_enters_after_drive_and_higher_low():
    # opening drive up, then a pullback whose low holds above the prior pullback,
    # then a bar reclaims back above the 9-EMA -> entry
    day = make_day(session_closes=[9.6, 10.4, 10.2, 10.1, 10.6, 10.9],
                   session_highs=[9.7, 10.5, 10.3, 10.2, 10.7, 11.0],
                   session_lows=[9.3, 10.0, 10.0, 10.05, 10.3, 10.6])
    ctx = build_context(day)
    trade = FirstPullback().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "first_pullback"

def test_first_pullback_no_entry_without_drive():
    day = make_day(session_closes=[9.4, 9.3, 9.35, 9.3, 9.32, 9.31],
                   session_highs=[9.5, 9.4, 9.45, 9.4, 9.42, 9.41],
                   session_lows=[9.2, 9.2, 9.25, 9.2, 9.22, 9.21])
    ctx = build_context(day)
    assert FirstPullback().evaluate(ctx, slip_bps=0.0) is None
