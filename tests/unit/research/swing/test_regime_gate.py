"""Tests for the SPY > 200-day-SMA regime gate.

The gate is causal (SMA uses only past closes) and gates ENTRIES ONLY — exits
must always be allowed to run, or a risk-off day would trap open positions.
"""
import datetime

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import new_state, portfolio_from_state
from src.lab.protocol import MarketContext
from src.lab.strategies import get_strategy
from scripts.research.swing.strategies import breakout_52w_trades, build_risk_on


def _days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def _spy(closes):
    idx = _days(len(closes))
    return pd.DataFrame({"close": closes}, index=idx)


# ── build_risk_on ────────────────────────────────────────────────────────

def test_risk_on_true_when_close_above_sma():
    # 10 bars, sma period 3: rising series -> close > sma once warm
    spy = _spy([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    m = build_risk_on(spy, sma_period=3)
    assert m[datetime.date(2024, 1, 10)] is True


def test_risk_on_false_when_close_below_sma():
    spy = _spy([10, 9, 8, 7, 6, 5, 4, 3, 2, 1])  # falling
    m = build_risk_on(spy, sma_period=3)
    assert m[datetime.date(2024, 1, 10)] is False


def test_risk_on_false_during_sma_warmup():
    """NaN SMA (not enough history) must read risk-OFF, never risk-on."""
    spy = _spy([1, 2, 3, 4, 5])
    m = build_risk_on(spy, sma_period=4)
    assert m[datetime.date(2024, 1, 1)] is False
    assert m[datetime.date(2024, 1, 2)] is False
    assert m[datetime.date(2024, 1, 3)] is False


def test_risk_on_is_causal_not_lookahead():
    """The flag for day i must not depend on any close after day i."""
    closes = [5, 5, 5, 5, 9, 1, 1, 1]
    full = build_risk_on(_spy(closes), sma_period=3)
    # truncating the future must not change an earlier day's verdict
    truncated = build_risk_on(_spy(closes[:5]), sma_period=3)
    assert full[datetime.date(2024, 1, 5)] == truncated[datetime.date(2024, 1, 5)]


# ── breakout_52w_trades gating ───────────────────────────────────────────

def _breakout_frame():
    """One clean fresh breakout at i=9 (2024-01-10).

    An early high (15) means the flat middle bars sit BELOW their lookback high,
    so the freshness filter (prior bar not already at a new high) is satisfied.
    The breakout can't be the final bar — the entry loop stops at n-1.
    """
    return pd.DataFrame(
        {
            "open":  [10.0] * 12,
            "high":  [15, 12, 11, 11, 11, 11, 11, 11, 11, 20, 21, 22.0],
            "low":   [9, 9, 9, 9, 9, 9, 9, 9, 9, 19, 20, 21.0],
            "close": [11, 10, 10, 10, 10, 10, 10, 10, 10, 20, 21, 22.0],
        },
        index=_days(12),
    )


def test_unfiltered_by_default_backwards_compatible():
    df = _breakout_frame()
    trades = breakout_52w_trades(df, "T", lookback=5, ma_exit=3)
    assert len(trades) == 1  # no risk_on passed -> no gating


def test_entry_skipped_when_risk_off():
    df = _breakout_frame()
    risk_off = {d.date(): False for d in df.index}
    trades = breakout_52w_trades(df, "T", lookback=5, ma_exit=3, risk_on=risk_off)
    assert trades == []


def test_entry_taken_when_risk_on():
    df = _breakout_frame()
    risk_on = {d.date(): True for d in df.index}
    trades = breakout_52w_trades(df, "T", lookback=5, ma_exit=3, risk_on=risk_on)
    assert len(trades) == 1


def test_missing_date_in_map_is_treated_as_risk_off():
    """Conservative default: unknown regime -> don't trade."""
    df = _breakout_frame()
    trades = breakout_52w_trades(df, "T", lookback=5, ma_exit=3, risk_on={})
    assert trades == []


# ── lab day-step gating ─────────────────────────────────────────────────

BREAKOUT_DAY = datetime.date(2024, 1, 10)   # index 9


def _bars_for_step():
    """Same shape as _breakout_frame, truncated so the breakout IS today."""
    df = pd.DataFrame(
        {
            "open":  [10.0] * 10,
            "high":  [15, 12, 11, 11, 11, 11, 11, 11, 11, 20.0],
            "low":   [9, 9, 9, 9, 9, 9, 9, 9, 9, 19.0],
            "close": [11, 10, 10, 10, 10, 10, 10, 10, 10, 20.0],
        },
        index=_days(10),
    )
    return {"AAA": df}


def _step(state, bars, session, *, risk_on=None, lookback=5, ma_exit=3,
          stop_pct=0.08, slip_bps=15.0):
    """One lab day-step: plan then SimFill, mirroring the runners."""
    sliced = {
        sym: df[df.index.map(lambda ts: ts.date() <= session)]
        for sym, df in bars.items()
    }
    sliced = {s: d for s, d in sliced.items() if not d.empty}
    params = {
        "lookback": lookback,
        "ma_exit": ma_exit,
        "stop_pct": stop_pct,
        "risk_pct": 0.01,
        "slip_bps": slip_bps,
        "use_regime_gate": risk_on is not None,
    }
    if risk_on is not None:
        params["risk_on_override"] = risk_on
    portfolio = portfolio_from_state(state, session)
    market = MarketContext(bars_by_symbol=sliced, extras={}, now=session)
    intents = get_strategy("breakout_52w").plan(portfolio, market, params)
    return apply_intents(state, intents, market, stop_pct=stop_pct,
                         slip_bps=slip_bps, snapshot_equity=True)


def test_step_takes_entry_when_risk_on():
    st = _step(new_state(starting_equity=200.0), _bars_for_step(), BREAKOUT_DAY,
               risk_on=True)
    assert len(st["open_positions"]) == 1


def test_step_skips_entry_when_risk_off():
    st = _step(new_state(starting_equity=200.0), _bars_for_step(), BREAKOUT_DAY,
               risk_on=False)
    assert st["open_positions"] == []


def test_step_ungated_by_default():
    st = _step(new_state(starting_equity=200.0), _bars_for_step(), BREAKOUT_DAY)
    assert len(st["open_positions"]) == 1


def test_risk_off_still_processes_exits():
    """A risk-off day must NOT trap an open position — exits always run."""
    bars = _bars_for_step()
    st = _step(new_state(starting_equity=200.0), bars, BREAKOUT_DAY, risk_on=True)
    assert len(st["open_positions"]) == 1

    # next day gaps below the stop, on a RISK-OFF day
    nxt = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=pd.date_range("2024-01-11", periods=1, freq="D"),
    )
    bars["AAA"] = pd.concat([bars["AAA"], nxt])

    st = _step(st, bars, datetime.date(2024, 1, 11), risk_on=False)
    assert st["open_positions"] == []          # exited, not trapped
    assert len(st["closed_trades"]) == 1
    assert st["closed_trades"][0]["reason"] == "gap_stop"
