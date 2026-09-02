"""Closed-trade journal: R-multiple and append-only records."""
import json
from pathlib import Path

from src.lab.journal import append_closed_trade, r_multiple


def test_r_multiple_is_exit_minus_entry_over_initial_risk():
    # entry 10, stop 9, exit 15 → 5R
    assert r_multiple(entry=10.0, exit_price=15.0, initial_stop=9.0) == 5.0


def test_r_multiple_is_minus_one_on_a_full_stop():
    assert r_multiple(entry=10.0, exit_price=9.0, initial_stop=9.0) == -1.0


def test_r_multiple_zero_when_stop_equals_entry():
    assert r_multiple(entry=10.0, exit_price=11.0, initial_stop=10.0) == 0.0


def test_append_closed_trade_writes_r_and_regime(tmp_path: Path):
    path = tmp_path / "journal.json"
    rec = {
        "symbol": "AAA",
        "entry_date": "2026-08-01",
        "exit_date": "2026-08-10",
        "entry_price": 10.0,
        "exit_price": 15.0,
        "qty": 2.0,
        "initial_stop": 9.0,
        "reason": "trail",
        "regime": {"spy_vs_sma200": 0.08, "risk_on": True},
    }
    append_closed_trade(path, rec)
    append_closed_trade(path, {**rec, "symbol": "BBB", "exit_price": 9.0})
    data = json.loads(path.read_text())
    assert len(data) == 2
    assert data[0]["r_multiple"] == 5.0
    assert data[0]["regime"]["risk_on"] is True
    assert data[1]["symbol"] == "BBB"
    assert data[1]["r_multiple"] == -1.0
