"""Flatten the live mega-cap book into cash at the open."""
from scripts.flatten_positions import flatten_plan


def test_flatten_plan_sells_every_long():
    positions = [
        {"symbol": "JPM", "qty": 0.0717, "avg_entry_price": 348.67, "current_price": 361.0},
        {"symbol": "KO", "qty": 0.2823, "avg_entry_price": 89.20, "current_price": 87.0},
    ]
    plan = flatten_plan(positions)
    assert [(o["action"], o["symbol"], o["qty"]) for o in plan] == [
        ("sell", "JPM", 0.0717),
        ("sell", "KO", 0.2823),
    ]
    assert all(o["reason"] == "flatten" for o in plan)


def test_flatten_plan_skips_zero_qty():
    plan = flatten_plan([
        {"symbol": "X", "qty": 0.0, "avg_entry_price": 1.0, "current_price": 1.0},
        {"symbol": "Y", "qty": 1.5, "avg_entry_price": 2.0, "current_price": 2.1},
    ])
    assert [o["symbol"] for o in plan] == ["Y"]


def test_flatten_plan_empty_book_is_empty():
    assert flatten_plan([]) == []


# ── journalling (added 2026-09-02) ─────────────────────────────────────────

def test_flatten_journals_each_filled_sell(tmp_path):
    """The flatten script is an exit path; its trades must not vanish."""
    import datetime
    import json as _json

    from scripts.flatten_positions import journal_flatten_results

    journal = tmp_path / "journal.json"
    meta = {"ABC": {"entry_date": "2026-08-01", "entry_price": 10.0, "initial_stop": 9.0}}
    results = [{"status": "submitted", "action": "sell", "symbol": "ABC",
                "qty": 2.0, "price": 12.0}]
    journal_flatten_results(results, meta, journal, datetime.date(2026, 9, 2))

    rows = _json.loads(journal.read_text())
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ABC"
    assert rows[0]["reason"] == "flatten"
    assert rows[0]["r_multiple"] == 2.0      # (12-10)/(10-9)


def test_flatten_does_not_journal_a_rejected_sell(tmp_path):
    import datetime

    from scripts.flatten_positions import journal_flatten_results

    journal = tmp_path / "journal.json"
    results = [{"status": "rejected", "action": "sell", "symbol": "ABC",
                "qty": 2.0, "price": 12.0, "error": "boom"}]
    journal_flatten_results(results, {}, journal, datetime.date(2026, 9, 2))
    assert not journal.exists()


def test_flatten_journals_a_position_with_no_meta(tmp_path):
    """Unknown initial stop must not silently discard the trade."""
    import datetime
    import json as _json

    from scripts.flatten_positions import journal_flatten_results

    journal = tmp_path / "journal.json"
    results = [{"status": "submitted", "action": "sell", "symbol": "ZZZ",
                "qty": 1.0, "price": 4.0}]
    journal_flatten_results(results, {}, journal, datetime.date(2026, 9, 2))
    rows = _json.loads(journal.read_text())
    assert rows[0]["symbol"] == "ZZZ"
    assert rows[0]["r_multiple"] is None
