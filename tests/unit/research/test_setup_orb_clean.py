from scripts.research.indicators_ctx import build_context
from scripts.research.setups.orb_clean import ORBClean
from tests.unit.research.fixtures import make_day

def test_orb_clean_enters_on_close_above_or_high():
    # OR high ~9.6 from first 3 session bars; bar 4 closes above at 9.9 -> entry
    day = make_day(session_closes=[9.4, 9.2, 9.4, 9.9, 10.5, 10.4],
                   session_highs=[9.5, 9.3, 9.6, 10.0, 10.6, 10.5],
                   session_lows=[8.9, 9.0, 9.1, 9.5, 10.0, 10.0])
    ctx = build_context(day)
    trade = ORBClean().evaluate(ctx, slip_bps=0.0)
    assert trade is not None
    assert trade.setup == "orb_clean"
    # stop = breakout-bar low (9.5), entry = breakout close (9.9)
    assert trade.stop == 9.5

def test_orb_clean_no_entry_when_never_breaks():
    day = make_day(session_closes=[9.4, 9.2, 9.3, 9.4, 9.3, 9.2],
                   session_highs=[9.5, 9.3, 9.6, 9.5, 9.4, 9.3],
                   session_lows=[8.9, 9.0, 9.1, 9.0, 9.0, 8.9])
    ctx = build_context(day)
    assert ORBClean().evaluate(ctx, slip_bps=0.0) is None
