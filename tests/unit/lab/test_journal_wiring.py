"""Regime capture at entry and closed-trade journalling.

The convex-breakout design is accepted or rejected on skew: max winner in R,
payoff ratio, and expectancy split by regime. None of that is computable unless
every closed trade is recorded with its initial stop and the regime it was
entered in — so these tests pin the measurement path, not the decision path.
"""
from __future__ import annotations

import datetime
import json

import pandas as pd

from src.lab.fills.sim import apply_intents
from src.lab.ledger import new_state
from src.lab.protocol import MarketContext, PortfolioView, PositionView, Side
from src.lab.strategies._common import regime_snapshot
from src.lab.strategies.breakout_52w import Breakout52wStrategy


def _days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


def _breakout_df(lookback=5):
    n = lookback + 1
    return pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [11.0] * (n - 2) + [10.0, 20.0],
            "low": [9.0] * n,
            "close": [10.0] * (n - 2) + [9.5, 20.0],
        },
        index=_days(n),
    )


def _spy(closes, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"close": closes}, index=idx)


# ── regime_snapshot ────────────────────────────────────────────────────────

def test_regime_snapshot_reports_risk_on_and_distance():
    # SMA3 of the last three closes (8,9,10) = 9.0; spot 10 → +11.1% above
    spy = _spy([5, 6, 7, 8, 9, 10])
    snap = regime_snapshot(spy, sma_period=3, as_of=datetime.date(2024, 1, 6))
    assert snap["risk_on"] is True
    assert abs(snap["spy_vs_sma200"] - (10.0 / 9.0 - 1.0)) < 1e-9


def test_regime_snapshot_reports_risk_off_below_the_average():
    spy = _spy([10, 9, 8, 7, 6, 5])
    snap = regime_snapshot(spy, sma_period=3, as_of=datetime.date(2024, 1, 6))
    assert snap["risk_on"] is False
    assert snap["spy_vs_sma200"] < 0


def test_regime_snapshot_is_none_during_sma_warmup():
    """Not enough history is unknown regime, never a fabricated 0.0."""
    spy = _spy([1, 2])
    assert regime_snapshot(spy, sma_period=200, as_of=datetime.date(2024, 1, 2)) is None


def test_regime_snapshot_is_none_without_spy():
    assert regime_snapshot(None, sma_period=200, as_of=datetime.date(2024, 1, 2)) is None


# ── strategy tags the entry ────────────────────────────────────────────────

def test_buy_intent_carries_regime_at_entry():
    df = _breakout_df(5)
    today = df.index[-1].date()
    spy = _spy([5, 6, 7, 8, 9, 10])
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=200.0, positions=[])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={"SPY": spy}, now=today)
    params = {
        "lookback": 5, "ma_exit": 3, "stop_pct": 0.08, "risk_pct": 0.01,
        "use_regime_gate": True, "regime_sma": 3,
    }
    buys = [i for i in Breakout52wStrategy().plan(portfolio, market, params)
            if i.side == Side.BUY]
    assert len(buys) == 1
    assert buys[0].metadata["regime"]["risk_on"] is True
    assert buys[0].metadata["regime"]["spy_vs_sma200"] > 0


def test_exit_intent_does_not_claim_a_regime():
    """Regime is a property of the entry; stamping it on the exit would lie."""
    df = _breakout_df(5)
    today = df.index[-1].date()
    pos = PositionView(
        symbol="AAA", qty=1.0, avg_entry_price=100.0, notional=100.0,
        entry_date=df.index[0].date(), stop_price=99.0,
    )
    portfolio = PortfolioView(as_of=today, equity=200.0, available_cash=0.0, positions=[pos])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={}, now=today)
    params = {"lookback": 5, "ma_exit": 3, "stop_pct": 0.08, "risk_pct": 0.01,
              "risk_on_override": True}
    sells = [i for i in Breakout52wStrategy().plan(portfolio, market, params)
             if i.side == Side.SELL]
    assert len(sells) == 1
    assert "regime" not in sells[0].metadata


# ── SimFill carries it through to the closed trade ─────────────────────────

def test_closed_trade_records_regime_from_entry():
    df = _breakout_df(5)
    entry_day = df.index[-1].date()
    spy = _spy([5, 6, 7, 8, 9, 10])

    state = new_state(starting_equity=200.0)
    portfolio = PortfolioView(as_of=entry_day, equity=200.0, available_cash=200.0, positions=[])
    market = MarketContext(bars_by_symbol={"AAA": df}, extras={"SPY": spy}, now=entry_day)
    params = {"lookback": 5, "ma_exit": 3, "stop_pct": 0.08, "risk_pct": 0.01,
              "use_regime_gate": True, "regime_sma": 3}
    intents = Breakout52wStrategy().plan(portfolio, market, params)
    state = apply_intents(state, intents, market, stop_pct=0.08, slip_bps=0.0)
    assert len(state["open_positions"]) == 1
    assert state["open_positions"][0]["metadata"]["regime"]["risk_on"] is True

    # next day gaps through the stop → position closes
    exit_day = entry_day + datetime.timedelta(days=1)
    nxt = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=pd.date_range(exit_day, periods=1, freq="D"),
    )
    df2 = pd.concat([df, nxt])
    pos = state["open_positions"][0]
    view = PositionView(
        symbol="AAA", qty=1.0, avg_entry_price=pos["entry_price"],
        notional=pos["notional"], entry_date=entry_day, stop_price=pos["stop_price"],
    )
    portfolio2 = PortfolioView(as_of=exit_day, equity=200.0, available_cash=0.0,
                               positions=[view])
    market2 = MarketContext(bars_by_symbol={"AAA": df2}, extras={}, now=exit_day)
    intents2 = Breakout52wStrategy().plan(portfolio2, market2, params)
    state = apply_intents(state, intents2, market2, stop_pct=0.08, slip_bps=0.0)

    assert len(state["closed_trades"]) == 1
    closed = state["closed_trades"][0]
    assert closed["regime"]["risk_on"] is True
    assert closed["initial_stop"] == pos["stop_price"]


# ── the live path writes a journal entry on exit ───────────────────────────

def test_live_sell_appends_to_journal_before_dropping_meta(tmp_path):
    from scripts.live_swing import record_closed_trades

    journal = tmp_path / "journal.json"
    meta = {
        "AAA": {
            "entry_date": "2026-08-01",
            "entry_price": 10.0,
            "initial_stop": 9.0,
            "regime": {"risk_on": True, "spy_vs_sma200": 0.08},
        }
    }
    results = [
        {"status": "submitted", "action": "sell", "symbol": "AAA",
         "qty": 2.0, "price": 15.0, "reason": "trail"},
    ]
    record_closed_trades(results, meta, journal, datetime.date(2026, 8, 10))

    rows = json.loads(journal.read_text())
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["r_multiple"] == 5.0          # (15-10)/(10-9)
    assert rows[0]["reason"] == "trail"
    assert rows[0]["regime"]["risk_on"] is True
    assert rows[0]["entry_date"] == "2026-08-01"
    assert rows[0]["exit_date"] == "2026-08-10"


def test_live_rejected_sell_is_not_journalled(tmp_path):
    from scripts.live_swing import record_closed_trades

    journal = tmp_path / "journal.json"
    meta = {"AAA": {"entry_date": "2026-08-01", "entry_price": 10.0, "initial_stop": 9.0}}
    results = [{"status": "rejected", "action": "sell", "symbol": "AAA",
                "qty": 2.0, "price": 15.0, "reason": "trail",
                "error": "RuntimeError: nope"}]
    record_closed_trades(results, meta, journal, datetime.date(2026, 8, 10))
    assert not journal.exists()


def test_live_sell_without_meta_is_still_journalled(tmp_path):
    """A position opened before the audit file existed must not vanish silently."""
    from scripts.live_swing import record_closed_trades

    journal = tmp_path / "journal.json"
    results = [{"status": "submitted", "action": "sell", "symbol": "ZZZ",
                "qty": 1.0, "price": 5.0, "reason": "stop"}]
    record_closed_trades(results, {}, journal, datetime.date(2026, 8, 10))

    rows = json.loads(journal.read_text())
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ZZZ"
    assert rows[0]["r_multiple"] is None      # unknowable without the initial stop
    assert rows[0]["entry_date"] is None


def test_live_plan_carries_regime_from_intent_to_order():
    """Without this hop the live audit records regime=None on every entry."""
    from src.lab.fills.broker import intents_to_live_plan
    from src.lab.protocol import OrderIntent

    regime = {"risk_on": True, "spy_vs_sma200": 0.08}
    intent = OrderIntent(
        symbol="AAA", side=Side.BUY, reason="fresh_breakout",
        stop_price=9.0, risk_pct=0.01,
        metadata={"entry_price": 10.0, "initial_stop": 9.0, "regime": regime},
    )
    plan = intents_to_live_plan(
        [intent], equity=200.0, available_cash=200.0, stop_pct=0.08,
        prices={"AAA": 10.0}, cash_buffer_pct=0.0,
    )
    assert len(plan) == 1
    assert plan[0]["regime"] == regime
