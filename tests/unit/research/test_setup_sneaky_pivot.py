"""Tests for the SneakyPivot setup."""
import pandas as pd
from tests.unit.research.fixtures import make_day
from scripts.research.indicators_ctx import build_context
from scripts.research.setups.sneaky_pivot import SneakyPivot


def _ctx_with_levels(session_closes, session_highs, session_lows,
                     session_opens=None, prev_low=None, prev_high=None):
    """Build a Ctx with optional prev_low/prev_high levels."""
    df = make_day(session_closes, session_highs, session_lows,
                  session_opens=session_opens)
    levels = {}
    if prev_low is not None:
        levels["prev_low"] = prev_low
    if prev_high is not None:
        levels["prev_high"] = prev_high
    return build_context(df, levels=levels if levels else None)


def test_sneaky_pivot_no_prev_low_returns_none():
    """When ctx.prev_low is None the setup must return None."""
    # 10 bars, no prev_low
    n = 10
    ctx = _ctx_with_levels(
        session_closes=[9.0] * n,
        session_highs=[9.5] * n,
        session_lows=[8.5] * n,
    )
    assert ctx.prev_low is None
    setup = SneakyPivot()
    assert setup.evaluate(ctx) is None


def test_sneaky_pivot_fewer_than_3_candles_returns_none():
    """Fewer than 9 session bars (< 3 fifteen-min candles) → None."""
    # 7 bars = 2 full 15-min candles + 1 bar into candle 2 (not complete)
    session_closes = [9.0, 8.8, 9.1, 9.3, 9.4, 9.6, 9.7]
    ctx = _ctx_with_levels(
        session_closes=session_closes,
        session_highs=[9.2, 9.1, 9.3, 9.4, 9.5, 9.7, 9.8],
        session_lows=[8.8, 8.7, 8.9, 9.1, 9.2, 9.3, 9.4],
        session_opens=[9.1, 9.0, 8.9, 9.2, 9.3, 9.4, 9.6],
        prev_low=9.0,
        prev_high=11.0,
    )
    setup = SneakyPivot()
    assert setup.evaluate(ctx) is None


def test_sneaky_pivot_candle0_does_not_tap_prev_low_returns_none():
    """If candle0.low > prev_low the setup should return None (no tap)."""
    # 10 session bars. candle0 lows all above prev_low=8.0
    n = 10
    ctx = _ctx_with_levels(
        session_closes=[9.5, 9.4, 9.6, 9.7, 9.8, 9.9, 9.6, 9.5, 9.4, 9.8],
        session_highs=[9.7, 9.6, 9.8, 9.9, 10.0, 10.1, 9.8, 9.7, 9.6, 10.0],
        session_lows=[9.3, 9.2, 9.4, 9.5, 9.6, 9.7, 9.4, 9.3, 9.2, 9.6],
        session_opens=[9.5, 9.4, 9.5, 9.6, 9.7, 9.8, 9.7, 9.6, 9.5, 9.7],
        prev_low=8.0,  # c0 lows are ~9.2, well above 8.0
        prev_high=11.0,
    )
    setup = SneakyPivot()
    assert setup.evaluate(ctx) is None


def test_sneaky_pivot_candle1_not_green_returns_none():
    """If candle1.close <= candle1.open the setup should return None."""
    # Candle 0 taps prev_low, but candle 1 is red (close < open)
    # 10 bars
    # c0 bars 0-2: lows touch prev_low=9.0, highs fine
    # c1 bars 3-5: red candle (close < open)
    ctx = _ctx_with_levels(
        session_closes=[9.1, 8.9, 9.2,  9.0, 8.8, 9.1,  9.3, 9.4, 9.5, 9.7],
        session_highs= [9.3, 9.1, 9.4,  9.2, 9.1, 9.3,  9.5, 9.6, 9.7, 9.9],
        session_lows=  [8.9, 8.8, 9.0,  8.7, 8.6, 8.8,  9.1, 9.2, 9.3, 9.5],
        session_opens= [9.2, 9.0, 9.0,  9.3, 9.1, 9.2,  9.2, 9.3, 9.4, 9.6],
        # c1 open=9.3, close=9.1 -> red
        prev_low=9.0,
        prev_high=11.0,
    )
    setup = SneakyPivot()
    assert setup.evaluate(ctx) is None


def test_sneaky_pivot_no_entry_bar_returns_none():
    """If no bar after c1 crosses sneaky_high, return None."""
    # c0 taps prev_low, c1 is green, but subsequent bars never hit sneaky_high
    # sneaky_high = c1.high = max of bars 3-5 highs = 9.5
    # bars 6+ highs all below 9.5
    ctx = _ctx_with_levels(
        session_closes=[9.1, 8.9, 9.2,  9.3, 9.4, 9.5,  9.3, 9.2, 9.1, 9.0],
        session_highs= [9.3, 9.1, 9.4,  9.4, 9.5, 9.5,  9.4, 9.3, 9.2, 9.1],
        session_lows=  [8.9, 8.8, 9.0,  9.1, 9.2, 9.3,  9.1, 9.0, 8.9, 8.8],
        session_opens= [9.2, 9.0, 9.0,  9.2, 9.3, 9.4,  9.4, 9.3, 9.2, 9.1],
        prev_low=9.0,
        prev_high=11.0,
    )
    setup = SneakyPivot()
    assert setup.evaluate(ctx) is None


def test_sneaky_pivot_happy_path_returns_trade():
    """Full valid setup: c0 taps prev_low, c1 green, bar after c2 hits sneaky_high."""
    # prev_low = 9.0, prev_high = 11.0
    # Candle 0 (bars 0-2, 09:30 ET): lows dip to/below 9.0
    #   opens  = [9.1, 9.0, 8.9]
    #   highs  = [9.2, 9.1, 9.3]
    #   lows   = [8.8, 8.7, 8.9]   <- min = 8.7, which is <= 9.0  ✓
    #   closes = [9.0, 8.9, 9.2]
    # Candle 1 (bars 3-5, 09:45 ET): green (c1.close > c1.open)
    #   opens  = [9.2, 9.3, 9.4]
    #   highs  = [9.4, 9.5, 9.7]   <- c1.high = 9.7 (sneaky_high)
    #   lows   = [9.1, 9.2, 9.3]   <- c1.low = 9.1
    #   closes = [9.3, 9.4, 9.6]   <- c1.close = 9.6 > c1.open = 9.2  ✓
    # Candle 2 (bars 6-8, 10:00 ET): below sneaky_high
    #   opens  = [9.5, 9.4, 9.5]
    #   highs  = [9.6, 9.5, 9.6]   <- all < 9.7
    #   lows   = [9.3, 9.2, 9.3]
    #   closes = [9.5, 9.4, 9.5]
    # Bar 9 (10:15 ET): entry — high = 9.8 >= sneaky_high 9.7  ✓
    #   open=9.5, high=9.8, low=9.4, close=9.7
    # Bar 10: exit bar (held for eod or trail)
    #   open=9.7, high=9.9, low=9.5, close=9.8
    closes = [9.0, 8.9, 9.2,  9.3, 9.4, 9.6,  9.5, 9.4, 9.5,  9.7, 9.8]
    highs  = [9.2, 9.1, 9.3,  9.4, 9.5, 9.7,  9.6, 9.5, 9.6,  9.8, 9.9]
    lows   = [8.8, 8.7, 8.9,  9.1, 9.2, 9.3,  9.3, 9.2, 9.3,  9.4, 9.5]
    opens  = [9.1, 9.0, 8.9,  9.2, 9.3, 9.4,  9.5, 9.4, 9.5,  9.5, 9.7]

    ctx = _ctx_with_levels(
        session_closes=closes,
        session_highs=highs,
        session_lows=lows,
        session_opens=opens,
        prev_low=9.0,
        prev_high=11.0,
    )

    setup = SneakyPivot()
    trade = setup.evaluate(ctx, slip_bps=0.0)

    assert trade is not None, "Expected a trade to be returned"
    assert trade.setup == "sneaky_pivot"
    # stop = defended_low = min(c0.low, c1.low) = min(8.7, 9.1) = 8.7
    # (with slip_bps=0 stop field is the planned stop, not slipped)
    assert trade.stop == 8.7
    # entry price = sneaky_high = 9.7
    assert trade.entry == 9.7
