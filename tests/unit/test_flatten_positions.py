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
