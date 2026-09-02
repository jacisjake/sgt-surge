"""Audit reconciliation against actual broker fills.

live_swing writes entry_price and initial_stop at order time, using the price it
planned against. Orders are placed at 16:05, after the close, so they fill at the
next session's open — FRSH planned at 13.86 and filled at 13.652. Left alone, the
stop sits at the wrong level and every R-multiple is computed from a denominator
that never happened.
"""
from __future__ import annotations

from scripts.live_swing import reconcile_fill_prices


def _meta(entry=13.86, stop=12.833157142857143):
    m = {"FRSH": {"entry_date": "2026-08-28", "strategy": "breakout_52w"}}
    if entry is not None:
        m["FRSH"]["entry_price"] = entry
    if stop is not None:
        m["FRSH"]["initial_stop"] = stop
    return m


def _pos(symbol="FRSH", avg=13.652331275282, qty=1.8788):
    return [{"symbol": symbol, "qty": qty, "avg_entry_price": avg}]


def test_entry_price_is_rewritten_to_the_actual_fill():
    meta = _meta()
    changed = reconcile_fill_prices(meta, _pos())
    assert len(changed) == 1
    assert meta["FRSH"]["entry_price"] == 13.652331275282


def test_stop_preserves_the_planned_distance_fraction():
    """The ATR model produced a 7.4087% stop; that fraction must survive."""
    meta = _meta()
    reconcile_fill_prices(meta, _pos())
    frac = (13.86 - 12.833157142857143) / 13.86
    assert abs(meta["FRSH"]["initial_stop"] - 13.652331275282 * (1 - frac)) < 1e-9
    # and that is meaningfully different from what was recorded
    assert abs(meta["FRSH"]["initial_stop"] - 12.6409) < 0.001


def test_planned_values_are_kept_for_the_record():
    meta = _meta()
    reconcile_fill_prices(meta, _pos())
    assert meta["FRSH"]["planned_entry_price"] == 13.86
    assert abs(meta["FRSH"]["planned_initial_stop"] - 12.833157142857143) < 1e-9


def test_no_change_when_the_fill_matches_the_plan():
    meta = _meta(entry=13.652331275282, stop=12.64)
    changed = reconcile_fill_prices(meta, _pos())
    assert changed == []
    assert meta["FRSH"]["entry_price"] == 13.652331275282


def test_is_idempotent():
    meta = _meta()
    first = reconcile_fill_prices(meta, _pos())
    second = reconcile_fill_prices(meta, _pos())
    assert len(first) == 1
    assert second == []


def test_symbol_not_held_is_left_alone():
    meta = _meta()
    changed = reconcile_fill_prices(meta, _pos(symbol="OTHER", avg=5.0))
    assert changed == []
    assert meta["FRSH"]["entry_price"] == 13.86


def test_missing_initial_stop_updates_entry_but_invents_no_stop():
    meta = _meta(stop=None)
    changed = reconcile_fill_prices(meta, _pos())
    assert len(changed) == 1
    assert meta["FRSH"]["entry_price"] == 13.652331275282
    assert "initial_stop" not in meta["FRSH"]


def test_missing_entry_price_records_the_fill_without_touching_the_stop():
    meta = _meta(entry=None)
    reconcile_fill_prices(meta, _pos())
    assert meta["FRSH"]["entry_price"] == 13.652331275282
    assert meta["FRSH"]["initial_stop"] == 12.833157142857143


def test_zero_or_missing_fill_price_is_skipped():
    meta = _meta()
    assert reconcile_fill_prices(meta, [{"symbol": "FRSH", "qty": 1.0, "avg_entry_price": 0.0}]) == []
    assert reconcile_fill_prices(meta, [{"symbol": "FRSH", "qty": 1.0}]) == []
    assert meta["FRSH"]["entry_price"] == 13.86


def test_symbol_matching_is_case_insensitive():
    meta = {"frsh": {"entry_price": 13.86, "initial_stop": 12.833157142857143}}
    changed = reconcile_fill_prices(meta, _pos(symbol="FRSH"))
    assert len(changed) == 1
    assert meta["frsh"]["entry_price"] == 13.652331275282


def test_change_record_reports_both_prices():
    meta = _meta()
    changed = reconcile_fill_prices(meta, _pos())
    c = changed[0]
    assert c["symbol"] == "FRSH"
    assert c["planned_entry"] == 13.86
    assert c["actual_entry"] == 13.652331275282
