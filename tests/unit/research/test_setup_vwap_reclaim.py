from scripts.research.indicators_ctx import build_context
from scripts.research.setups.vwap_reclaim import VWAPReclaim
from tests.unit.research.fixtures import make_day

def test_vwap_reclaim_enters_on_reclaim_and_hold():
    # price dips below vwap then a bar closes back above vwap with low >= prior low
    day = make_day(session_closes=[10.0, 9.5, 9.4, 9.9, 10.2, 10.3],
                   session_highs=[10.1, 9.7, 9.6, 10.0, 10.3, 10.4],
                   session_lows=[9.8, 9.3, 9.2, 9.5, 10.0, 10.1])
    ctx = build_context(day)
    trade = VWAPReclaim().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "vwap_reclaim"

def test_vwap_reclaim_no_entry_when_always_below():
    day = make_day(session_closes=[10.0, 9.5, 9.3, 9.2, 9.1, 9.0],
                   session_highs=[10.1, 9.7, 9.5, 9.4, 9.3, 9.2],
                   session_lows=[9.8, 9.3, 9.1, 9.0, 8.9, 8.8])
    ctx = build_context(day)
    assert VWAPReclaim().evaluate(ctx, slip_bps=0.0) is None
