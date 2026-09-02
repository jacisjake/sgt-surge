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


def _atr_params(**kw):
    p = {
        "lookback": 5,
        "stop_pct": 0.08,
        "risk_pct": 0.01,
        "use_regime_gate": False,
        "risk_on_override": True,
        "k1": 2.0,
        "k2": 3.0,
        "atr_period": 2,
        "stop_min_pct": 0.04,
        "stop_max_pct": 0.15,
        "use_ma_exit": False,
    }
    p.update(kw)
    return p


def test_plan_atr_entry_stop_clamps_to_max():
    """Existing breakout frame has a huge TR on the breakout bar → 15% cap."""
    df = _breakout_df(5)
    today = df.index[-1].date()
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=200.0, positions=[])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={}, now=today)
    buys = [i for i in Breakout52wStrategy().plan(portfolio, market, _atr_params()) if i.side == Side.BUY]
    assert len(buys) == 1
    assert abs(buys[0].stop_price - 20.0 * 0.85) < 1e-9


def test_plan_does_not_exit_on_sma50_when_use_ma_exit_false():
    """Close below SMA3 but above the hard stop must stay open."""
    today = datetime.date(2024, 1, 10)
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 90.0],
            "high": [100.0, 100.0, 100.0, 95.0],
            "low": [100.0, 100.0, 100.0, 90.0],
            "close": [100.0, 100.0, 100.0, 91.0],
        },
        index=_days(4, start="2024-01-07"),
    )
    pos = PositionView(
        symbol="BBB",
        qty=1.0,
        avg_entry_price=100.0,
        entry_date=datetime.date(2024, 1, 7),
        stop_price=85.0,
        notional=100.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"BBB": df}, extras={}, now=today)
    intents = Breakout52wStrategy().plan(
        portfolio, market, _atr_params(risk_on_override=False, ma_exit=3)
    )
    assert [i for i in intents if i.side == Side.SELL] == []


def test_plan_exits_on_chandelier_trail():
    """Highest high 13, ATR 0.3, k2=3 → floor 12.1; low 12.0 tags the trail."""
    idx = _days(4, start="2024-01-07")
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.9, 12.9],
            "high": [11.0, 13.0, 13.0, 13.0],
            "low": [9.9, 10.9, 12.5, 12.0],
            "close": [10.9, 12.9, 12.8, 12.2],
            "atr": [0.3, 0.3, 0.3, 0.3],
        },
        index=idx,
    )
    today = idx[-1].date()
    pos = PositionView(
        symbol="CCC",
        qty=2.0,
        avg_entry_price=10.0,
        entry_date=idx[0].date(),
        stop_price=9.5,
        notional=20.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"CCC": df}, extras={}, now=today)
    sells = [
        i
        for i in Breakout52wStrategy().plan(portfolio, market, _atr_params(risk_on_override=False))
        if i.side == Side.SELL
    ]
    assert len(sells) == 1
    assert sells[0].reason == "trail"
    assert sells[0].stop_price is not None
    assert abs(sells[0].stop_price - 12.1) < 1e-9



def test_plan_gap_through_stop_is_gap_stop():
    today = datetime.date(2024, 1, 10)
    df = pd.DataFrame(
        {"open": [8.0], "high": [8.5], "low": [7.5], "close": [8.2]},
        index=_days(1, start="2024-01-10"),
    )
    pos = PositionView(
        symbol="DDD",
        qty=1.0,
        avg_entry_price=10.0,
        entry_date=datetime.date(2024, 1, 1),
        stop_price=9.0,
        notional=10.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"DDD": df}, extras={}, now=today)
    sells = [
        i
        for i in Breakout52wStrategy().plan(portfolio, market, _atr_params(risk_on_override=False))
        if i.side == Side.SELL
    ]
    assert len(sells) == 1
    assert sells[0].reason == "gap_stop"


def test_plan_never_emits_a_target_on_a_runner():
    """A position that has run ~10R stays open; no take-profit intent."""
    idx = _days(5, start="2024-01-06")
    df = pd.DataFrame(
        {
            "open": [10.0, 12.0, 15.0, 18.0, 21.3],
            "high": [12.0, 15.0, 18.0, 21.0, 22.0],
            "low": [9.8, 11.8, 14.8, 17.8, 21.2],
            "close": [11.8, 14.8, 17.8, 20.8, 21.5],
            "atr": [0.3, 0.3, 0.3, 0.3, 0.3],
        },
        index=idx,
    )
    today = idx[-1].date()
    pos = PositionView(
        symbol="EEE",
        qty=1.0,
        avg_entry_price=10.0,
        entry_date=idx[0].date(),
        stop_price=9.0,
        notional=10.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"EEE": df}, extras={}, now=today)
    intents = Breakout52wStrategy().plan(portfolio, market, _atr_params(risk_on_override=False))
    assert intents == []
    assert all(i.target_price is None for i in intents)
