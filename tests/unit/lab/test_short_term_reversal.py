"""ShortTermReversalStrategy state machine + SimFill PnL."""
from __future__ import annotations

import datetime

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import new_state, portfolio_from_paper
from src.lab.protocol import MarketContext, PortfolioView, PositionView, Side
from src.lab.strategies.short_term_reversal import (
    ShortTermReversalStrategy,
    sessions_after_entry,
)


def _days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def test_sessions_after_entry_entry_day_is_zero():
    df = pd.DataFrame({"close": [1, 2, 3]}, index=_days(3))
    d0 = df.index[0].date()
    assert sessions_after_entry(d0, d0, df) == 0
    assert sessions_after_entry(df.index[2].date(), d0, df) == 2


def test_no_exit_on_entry_day():
    today = datetime.date(2024, 1, 5)
    df = pd.DataFrame(
        {"open": [10], "high": [10], "low": [5], "close": [9]},
        index=_days(1, start="2024-01-05"),
    )
    pos = PositionView(
        symbol="AAA",
        qty=1.0,
        avg_entry_price=10.0,
        entry_date=today,
        stop_price=9.5,
        notional=10.0,
        metadata={"target_price": 11.0, "hold_bars": 5},
    )
    portfolio = PortfolioView(as_of=today, equity=200, available_cash=190, positions=[pos])
    market = MarketContext(bars_by_symbol={"AAA": df}, now=today)
    intents = ShortTermReversalStrategy().plan(
        portfolio, market, {"stop_pct": 0.05, "hold": 5}
    )
    assert intents == []


def test_time_exit_after_hold_sessions():
    """Entry day 0; after hold=2 sessions, time exit at close."""
    # bars: entry at day0, then day1, day2
    idx = _days(3, start="2024-01-01")
    df = pd.DataFrame(
        {
            "open": [10, 10, 10],
            "high": [10, 10, 10],
            "low": [10, 10, 10],
            "close": [10, 10, 10],
        },
        index=idx,
    )
    entry_date = idx[0].date()
    as_of = idx[2].date()  # sessions_after = 2
    pos = PositionView(
        symbol="AAA",
        qty=1.0,
        avg_entry_price=10.0,
        entry_date=entry_date,
        stop_price=9.0,
        notional=10.0,
        metadata={"target_price": 20.0, "hold_bars": 2},
    )
    portfolio = PortfolioView(as_of=as_of, equity=200, available_cash=190, positions=[pos])
    market = MarketContext(bars_by_symbol={"AAA": df}, now=as_of)
    intents = ShortTermReversalStrategy().plan(portfolio, market, {"hold": 2})
    sells = [i for i in intents if i.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].reason == "time"


def test_simfill_target_pnl_uses_ratio_not_trade_list():
    state = new_state(200.0)
    state["open_positions"] = [{
        "symbol": "AAA",
        "entry_date": "2024-01-01",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "notional": 25.0,
        "metadata": {"target_price": 110.0, "hold_bars": 5},
    }]
    state["available_cash"] = 175.0
    as_of = datetime.date(2024, 1, 3)
    df = pd.DataFrame(
        {
            "open": [100, 100, 100],
            "high": [100, 100, 115],
            "low": [100, 100, 100],
            "close": [100, 100, 112],
        },
        index=_days(3, start="2024-01-01"),
    )
    portfolio = portfolio_from_paper(state, as_of)
    market = MarketContext(bars_by_symbol={"AAA": df}, now=as_of)
    intents = ShortTermReversalStrategy().plan(
        portfolio, market, {"hold": 5, "stop_pct": 0.05, "target_pct": 0.10}
    )
    assert intents[0].reason == "target"
    state2 = apply_intents(state, intents, market, stop_pct=0.05, slip_bps=15.0)
    t = state2["closed_trades"][0]
    slip = 2 * 15.0 / 10_000
    expected = 25.0 * ((110.0 * (1 - slip)) / (100.0 * (1 + slip)) - 1)
    assert abs(t["pnl"] - expected) < 0.01
    # trade-list style would be different
    trade_list = 25.0 * (110 / 100 - 1 - slip)
    assert abs(t["pnl"] - trade_list) > 0.01
