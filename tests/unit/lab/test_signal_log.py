"""Append-only record of scanner hits, for measuring what follows them.

The momentum scanner has run for months and every candidate was discarded at
the daily reset. A gainers list ranked on having already moved always looks
good; the only way to know whether it predicts anything is to timestamp each
hit and measure forward returns later. Recording costs nothing and risks
nothing.
"""
from __future__ import annotations

import datetime
import json

from src.lab.signal_log import append_hits, load_hits


class _Cand:
    """Stand-in for MomentumCandidate — only the fields we record."""

    def __init__(self, symbol, price, change_pct=None, volume=None,
                 relative_volume=None, gap_pct=None, prev_close=None,
                 has_catalyst=False, passes_all_filters=False):
        self.symbol = symbol
        self.price = price
        self.change_pct = change_pct
        self.volume = volume
        self.relative_volume = relative_volume
        self.gap_pct = gap_pct
        self.prev_close = prev_close
        self.has_catalyst = has_catalyst
        self.passes_all_filters = passes_all_filters


def test_hits_are_appended_with_an_observation_timestamp(tmp_path):
    p = tmp_path / "signals.json"
    at = datetime.datetime(2026, 9, 2, 10, 58, 11)
    append_hits(p, [_Cand("BIAF", 9.24, 40.2, 20_716_367)], observed_at=at)

    rows = json.loads(p.read_text())
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BIAF"
    assert rows[0]["price"] == 9.24
    assert rows[0]["change_pct"] == 40.2
    assert rows[0]["volume"] == 20_716_367
    assert rows[0]["observed_at"] == "2026-09-02T10:58:11"


def test_appending_preserves_earlier_rows(tmp_path):
    p = tmp_path / "signals.json"
    at = datetime.datetime(2026, 9, 2, 10, 0)
    append_hits(p, [_Cand("AAA", 5.0)], observed_at=at)
    append_hits(p, [_Cand("BBB", 6.0)], observed_at=at.replace(hour=11))
    rows = json.loads(p.read_text())
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]


def test_the_same_symbol_is_not_recorded_twice_in_one_session(tmp_path):
    """A scanner running every few minutes would otherwise flood the log."""
    p = tmp_path / "signals.json"
    at = datetime.datetime(2026, 9, 2, 10, 0)
    append_hits(p, [_Cand("AAA", 5.0)], observed_at=at)
    append_hits(p, [_Cand("AAA", 5.4)], observed_at=at.replace(hour=11))
    rows = json.loads(p.read_text())
    assert len(rows) == 1
    assert rows[0]["price"] == 5.0      # the FIRST sighting is the signal


def test_the_same_symbol_is_recorded_again_on_a_later_session(tmp_path):
    p = tmp_path / "signals.json"
    append_hits(p, [_Cand("AAA", 5.0)],
                observed_at=datetime.datetime(2026, 9, 2, 10, 0))
    append_hits(p, [_Cand("AAA", 7.0)],
                observed_at=datetime.datetime(2026, 9, 3, 10, 0))
    rows = json.loads(p.read_text())
    assert len(rows) == 2


def test_liquidity_and_context_fields_are_kept(tmp_path):
    p = tmp_path / "signals.json"
    append_hits(p, [_Cand("NDRA", 6.42, 21.7, 57_453, relative_volume=3.1,
                          gap_pct=8.0, prev_close=5.28, has_catalyst=True,
                          passes_all_filters=False)],
                observed_at=datetime.datetime(2026, 9, 2, 10, 58))
    r = json.loads(p.read_text())[0]
    assert r["relative_volume"] == 3.1
    assert r["gap_pct"] == 8.0
    assert r["prev_close"] == 5.28
    assert r["has_catalyst"] is True
    assert r["passes_all_filters"] is False
    # dollar volume is what decides whether it is tradable at all
    assert abs(r["dollar_volume"] - 6.42 * 57_453) < 1e-6


def test_empty_scan_writes_nothing(tmp_path):
    p = tmp_path / "signals.json"
    append_hits(p, [], observed_at=datetime.datetime(2026, 9, 2, 10, 0))
    assert not p.exists()


def test_load_hits_on_missing_file_is_empty(tmp_path):
    assert load_hits(tmp_path / "nope.json") == []


def test_recording_never_raises_on_a_bad_candidate(tmp_path):
    """Logging is observational — it must never break the scan loop."""
    p = tmp_path / "signals.json"

    class Broken:
        symbol = "X"

        @property
        def price(self):
            raise RuntimeError("boom")

    append_hits(p, [Broken(), _Cand("OK", 1.0)],
                observed_at=datetime.datetime(2026, 9, 2, 10, 0))
    rows = json.loads(p.read_text())
    assert [r["symbol"] for r in rows] == ["OK"]
