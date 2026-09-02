"""Audit meta reconciliation against the broker.

A position can leave the book without passing through live_swing's sell branch
— a manual close, the flatten script, or a missed run. Before this, its meta
sat in the audit forever and the trade vanished from the evidence base: the
server audit held 18 symbols while the broker held 1.
"""
from __future__ import annotations

import datetime
import json

from scripts.live_swing import reconcile_audit_meta


def test_meta_for_a_symbol_no_longer_held_is_journalled_and_dropped(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {
        "FRSH": {"entry_date": "2026-08-28", "entry_price": 13.86, "initial_stop": 12.83},
        "IOVA": {"entry_date": "2026-08-20", "entry_price": 9.68, "initial_stop": 8.83},
    }
    dropped = reconcile_audit_meta(meta, {"FRSH"}, journal, datetime.date(2026, 9, 2))

    assert list(meta) == ["FRSH"]          # still held, untouched
    assert len(dropped) == 1
    rows = json.loads(journal.read_text())
    assert rows[0]["symbol"] == "IOVA"
    assert rows[0]["reason"] == "reconciled_unknown_exit"
    assert rows[0]["exit_price"] is None   # unrecoverable, never guessed
    assert rows[0]["r_multiple"] is None
    assert rows[0]["entry_price"] == 9.68  # what we do know is preserved


def test_nothing_dropped_when_every_symbol_is_still_held(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {"FRSH": {"entry_date": "2026-08-28", "entry_price": 13.86, "initial_stop": 12.83}}
    dropped = reconcile_audit_meta(meta, {"FRSH"}, journal, datetime.date(2026, 9, 2))
    assert dropped == []
    assert not journal.exists()


def test_reconcile_is_idempotent(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {"OLD": {"entry_date": "2026-08-01", "entry_price": 5.0, "initial_stop": 4.6}}
    reconcile_audit_meta(meta, set(), journal, datetime.date(2026, 9, 2))
    reconcile_audit_meta(meta, set(), journal, datetime.date(2026, 9, 2))
    assert len(json.loads(journal.read_text())) == 1   # not double-written


def test_held_comparison_is_case_insensitive(tmp_path):
    """Broker casing must never cause a held position to be journalled as closed."""
    journal = tmp_path / "journal.json"
    meta = {"frsh": {"entry_date": "2026-08-28", "entry_price": 13.86, "initial_stop": 12.83}}
    dropped = reconcile_audit_meta(meta, {"FRSH"}, journal, datetime.date(2026, 9, 2))
    assert dropped == []
    assert list(meta) == ["frsh"]


def test_regime_is_carried_into_the_reconciled_record(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {"OLD": {"entry_date": "2026-08-01", "entry_price": 5.0, "initial_stop": 4.6,
                    "regime": {"risk_on": True, "spy_vs_sma200": 0.03}}}
    reconcile_audit_meta(meta, set(), journal, datetime.date(2026, 9, 2))
    rows = json.loads(journal.read_text())
    assert rows[0]["regime"]["risk_on"] is True
