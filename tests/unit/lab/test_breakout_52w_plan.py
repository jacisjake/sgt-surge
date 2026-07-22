"""Decision tests for Breakout52wStrategy.plan."""
from __future__ import annotations

import datetime

import pandas as pd

from src.lab.protocol import MarketContext, PortfolioView, PositionView, Side
from src.lab.strategies.breakout_52w import Breakout52wStrategy


def _days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def _breakout_df(lookback=5):
    n = lookback + 1
    highs = [11.0] * (n - 2) + [10.0, 20.0]
    closes = [10.0] * (n - 2) + [9.5, 20.0]
    return pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": highs,
            "low": [9.0] * n,
            "close": closes,
        },
        index=_days(n),
    )


TODAY = datetime.date(2024, 1, 6)  # index 5 if start 2024-01-01 (6 days)


def test_plan_fresh_breakout_emits_buy_when_risk_on():
    df = _breakout_df(5)
    today = df.index[-1].date()
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=200.0, positions=[])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={}, now=today)
    params = {
        "lookback": 5,
        "ma_exit": 3,
        "stop_pct": 0.08,
        "risk_pct": 0.01,
        "use_regime_gate": False,
        "risk_on_override": True,
    }
    intents = Breakout52wStrategy().plan(portfolio, market, params)
    buys = [i for i in intents if i.side == Side.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "AAA"
    assert buys[0].reason == "fresh_breakout"
    assert buys[0].risk_pct == 0.01
    assert abs(buys[0].metadata["entry_price"] - 20.0) < 1e-9


def test_plan_skips_entries_when_risk_off():
    df = _breakout_df(5)
    today = df.index[-1].date()
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=200.0, positions=[])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={}, now=today)
    params = {
        "lookback": 5,
        "ma_exit": 3,
        "stop_pct": 0.08,
        "risk_pct": 0.01,
        "risk_on_override": False,
    }
    intents = Breakout52wStrategy().plan(portfolio, market, params)
    assert [i for i in intents if i.side == Side.BUY] == []


def test_plan_exit_on_stop_even_when_risk_off():
    today = datetime.date(2024, 1, 10)
    df = pd.DataFrame(
        {"open": [100.0], "high": [100.0], "low": [80.0], "close": [85.0]},
        index=_days(1, start="2024-01-10"),
    )
    pos = PositionView(
        symbol="BBB",
        qty=0.5,
        avg_entry_price=100.0,
        entry_date=datetime.date(2024, 1, 1),
        stop_price=92.0,
        notional=50.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"BBB": df}, extras={}, now=today)
    intents = Breakout52wStrategy().plan(
        portfolio,
        market,
        {"ma_exit": 3, "stop_pct": 0.08, "risk_on_override": False},
    )
    sells = [i for i in intents if i.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].reason == "stop"
    assert sells[0].qty == 0.5
