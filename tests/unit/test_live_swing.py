"""Tests for the live swing order planner (scripts/live_swing.plan_orders)."""
import datetime

import pandas as pd

from scripts.live_swing import plan_orders


def _days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def _breakout_df():
    """Fresh breakout on the last bar (index 9 = 2024-01-10)."""
    return pd.DataFrame(
        {
            "open":  [10.0] * 10,
            "high":  [15, 12, 11, 11, 11, 11, 11, 11, 11, 20.0],
            "low":   [9, 9, 9, 9, 9, 9, 9, 9, 9, 19.0],
            "close": [11, 10, 10, 10, 10, 10, 10, 10, 10, 20.0],
        },
        index=_days(10),
    )


def _spy(above=True):
    base = 100.0
    closes = [base] * 9 + [base * (1.10 if above else 0.90)]
    return pd.DataFrame({"close": closes}, index=_days(10))


TODAY = datetime.date(2024, 1, 10)
P = dict(lookback=5, ma_exit=3, stop_pct=0.08, regime_sma=3)


def test_fresh_breakout_entry_when_risk_on():
    plan = plan_orders([], {"AAA": _breakout_df()}, _spy(above=True),
                       equity=200.0, available_cash=200.0, today=TODAY, **P)
    buys = [o for o in plan if o["action"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["symbol"] == "AAA"


def test_no_entry_when_risk_off():
    plan = plan_orders([], {"AAA": _breakout_df()}, _spy(above=False),
                       equity=200.0, available_cash=200.0, today=TODAY, **P)
    assert [o for o in plan if o["action"] == "buy"] == []


def test_entry_is_fractional_on_small_account():
    """$200 account, 1% risk / 8% stop -> ~$25 notional / $20 price = ~1.24 sh."""
    plan = plan_orders([], {"AAA": _breakout_df()}, _spy(above=True),
                       equity=200.0, available_cash=200.0, today=TODAY,
                       risk_pct=0.01, **{k: v for k, v in P.items() if k != "stop_pct"},
                       stop_pct=0.08)
    buy = [o for o in plan if o["action"] == "buy"][0]
    # notional = min(0.01*200/0.08, 200) = 25 ; qty = 25/20 = 1.25
    assert abs(buy["qty"] - 1.25) < 1e-6
    assert buy["qty"] != int(buy["qty"])  # genuinely fractional


def test_exit_on_stop():
    """Held position whose price gapped below its 8% stop -> SELL."""
    df = pd.DataFrame(
        {"open": [100.0], "high": [100.0], "low": [80.0], "close": [85.0]},
        index=_days(1, start="2024-01-10"),
    )
    pos = [{"symbol": "BBB", "qty": 0.5, "avg_entry_price": 100.0, "current_price": 85.0}]
    plan = plan_orders(pos, {"BBB": df}, _spy(above=True),
                       equity=200.0, available_cash=0.0, today=TODAY, **P)
    sells = [o for o in plan if o["action"] == "sell"]
    assert len(sells) == 1
    assert sells[0]["reason"] == "stop"
    assert sells[0]["qty"] == 0.5


def test_exits_run_even_when_risk_off():
    """A risk-off day must still allow exits — never trap a position."""
    df = pd.DataFrame(
        {"open": [100.0] * 5, "high": [100.0] * 5, "low": [100, 100, 100, 100, 80.0],
         "close": [100, 100, 100, 100, 85.0]},
        index=_days(5, start="2024-01-06"),
    )
    pos = [{"symbol": "BBB", "qty": 0.5, "avg_entry_price": 100.0, "current_price": 85.0}]
    plan = plan_orders(pos, {"BBB": df}, _spy(above=False),
                       equity=200.0, available_cash=0.0, today=datetime.date(2024, 1, 10), **P)
    assert [o for o in plan if o["action"] == "sell"][0]["reason"] == "stop"


def test_does_not_rebuy_held_symbol():
    pos = [{"symbol": "AAA", "qty": 1.0, "avg_entry_price": 15.0, "current_price": 20.0}]
    plan = plan_orders(pos, {"AAA": _breakout_df()}, _spy(above=True),
                       equity=200.0, available_cash=200.0, today=TODAY, **P)
    assert [o for o in plan if o["action"] == "buy" and o["symbol"] == "AAA"] == []
