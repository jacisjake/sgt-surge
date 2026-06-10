from scripts.research.metrics import expectancy, profit_factor, max_drawdown_r, summarize

def _rs(*vals):  # list of r-multiples
    return list(vals)

def test_expectancy_basic():
    # 2 wins +2R, 2 losses -1R -> mean = (2+2-1-1)/4 = 0.5
    assert expectancy(_rs(2, 2, -1, -1)) == 0.5

def test_profit_factor():
    # gross win 4, gross loss 2 -> 2.0
    assert profit_factor(_rs(2, 2, -1, -1)) == 2.0

def test_max_drawdown_r():
    # cum: 2,1,3,0 ... peak 3 then 0 -> dd 3? walk: +2,-1,+2,-3 -> equity 2,1,3,0 peak3 trough0 dd=3
    assert max_drawdown_r(_rs(2, -1, 2, -3)) == 3.0

def test_summarize_shape():
    s = summarize("orb_clean", _rs(2, -1, 2, -1))
    assert s["setup"] == "orb_clean"
    assert s["n"] == 4
    assert s["win_pct"] == 0.5
    assert round(s["expectancy"], 3) == 0.5
    assert s["profit_factor"] == 2.0
