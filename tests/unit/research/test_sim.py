import pandas as pd
from scripts.research.sim import Trade, make_trade, simulate_exit

def _bars(rows):  # rows: list of (open, high, low, close, atr)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "atr"])

def test_make_trade_applies_slippage_and_computes_r():
    # entry 10, stop 9 -> planned risk 1. exit 12.
    # slip 100 bps: entry fill 10.10, exit fill 11.88 -> pnl 1.78 -> r 1.78
    t = make_trade("X", "2026-06-09", "orb_clean", 10.0, 9.0, 12.0, "trail", 5, slip_bps=100.0)
    assert isinstance(t, Trade)
    assert round(t.r_multiple, 2) == 1.78
    assert t.exit_reason == "trail"

def test_simulate_exit_stops_out_intrabar():
    # entry 10 stop 9.5; second bar low 9.4 -> exit at stop 9.5
    bars = _bars([(10.0, 10.2, 9.8, 10.1, 0.3), (10.0, 10.1, 9.4, 9.6, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 9.5 and reason == "stop" and held == 2

def test_simulate_exit_gap_through_fills_at_open():
    # bar opens 9.0 below stop 9.5 -> fill at open 9.0
    bars = _bars([(10.0, 10.2, 9.8, 10.1, 0.3), (9.0, 9.1, 8.8, 8.9, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 9.0 and reason == "gap_stop"

def test_simulate_exit_chandelier_trails_up():
    # price runs to 13 (atr 0.3, k=3 -> chandelier floor 13-0.9=12.1),
    # then a bar dips to 12.0 -> trail exit at 12.1
    bars = _bars([
        (10.0, 11.0, 9.9, 10.9, 0.3),
        (11.0, 13.0, 10.9, 12.9, 0.3),
        (12.9, 13.0, 12.0, 12.2, 0.3),
    ])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert round(px, 2) == 12.1 and reason == "trail"

def test_simulate_exit_force_flat_at_end():
    bars = _bars([(10.0, 10.5, 9.9, 10.4, 0.3), (10.4, 10.6, 10.2, 10.5, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 10.5 and reason == "eod" and held == 2


def test_simulate_exit_target_hit_before_stop():
    # entry 10.0, stop 9.5, target 11.5
    # bar 1: high 11.0 < target -> no target hit yet
    # bar 2: high 12.0 >= target 11.5 -> exit at target
    bars = _bars([
        (10.0, 11.0, 9.8, 10.9, 0.3),
        (11.0, 12.0, 10.8, 11.9, 0.3),
    ])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5,
                                     k=3.0, target=11.5)
    assert px == 11.5 and reason == "target" and held == 2


def test_simulate_exit_stop_before_target_same_bar():
    # bar has low <= stop AND high >= target: stop takes precedence (conservative)
    bars = _bars([
        (10.0, 12.0, 9.0, 11.0, 0.3),  # low 9.0 <= stop 9.5 AND high 12.0 >= target 11.5
    ])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5,
                                     k=3.0, target=11.5)
    assert reason == "stop"


def test_simulate_exit_no_target_unchanged():
    # target=None is same as before
    bars = _bars([(10.0, 10.5, 9.9, 10.4, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5,
                                     k=3.0, target=None)
    assert reason == "eod"
