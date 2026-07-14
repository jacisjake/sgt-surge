"""Tests for Schwab refresh-token expiry tracking."""
import datetime as dt
import json

import pytest

from src.core.schwab_token import (
    REFRESH_TOKEN_LIFETIME,
    needs_attention,
    read_token_status,
    status_from_payload,
)

UTC = dt.timezone.utc


def _payload(created: dt.datetime) -> dict:
    return {"creation_timestamp": int(created.timestamp()), "token": {"refresh_token": "x"}}


def test_lifetime_is_seven_days():
    assert REFRESH_TOKEN_LIFETIME == dt.timedelta(days=7)


def test_fresh_token_has_full_life():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    st = status_from_payload(_payload(now), now)
    assert st["present"] is True
    assert st["expired"] is False
    assert st["days_remaining"] == pytest.approx(7.0, abs=1e-6)


def test_expiry_is_creation_plus_seven_days():
    created = dt.datetime(2026, 6, 30, 16, 22, 18, tzinfo=UTC)
    now = created + dt.timedelta(days=1)
    st = status_from_payload(_payload(created), now)
    assert st["expires_at"] == (created + dt.timedelta(days=7)).isoformat()
    assert st["days_remaining"] == pytest.approx(6.0, abs=1e-6)


def test_token_past_seven_days_is_expired():
    created = dt.datetime(2026, 6, 30, tzinfo=UTC)
    now = created + dt.timedelta(days=7, seconds=1)
    st = status_from_payload(_payload(created), now)
    assert st["expired"] is True
    assert st["days_remaining"] < 0


def test_exactly_at_expiry_is_expired():
    created = dt.datetime(2026, 6, 30, tzinfo=UTC)
    now = created + REFRESH_TOKEN_LIFETIME
    assert status_from_payload(_payload(created), now)["expired"] is True


def test_missing_payload_reads_as_absent_and_expired():
    st = status_from_payload(None, dt.datetime.now(UTC))
    assert st["present"] is False
    assert st["expired"] is True
    assert st["expires_at"] is None


def test_payload_without_creation_timestamp_is_absent():
    st = status_from_payload({"token": {}}, dt.datetime.now(UTC))
    assert st["present"] is False


# ── needs_attention: the alerting decision ──────────────────────────────

def test_needs_attention_when_expired():
    created = dt.datetime(2026, 6, 30, tzinfo=UTC)
    st = status_from_payload(_payload(created), created + dt.timedelta(days=8))
    ok, level, _ = needs_attention(st, warn_within_days=2.0)
    assert ok is True
    assert level == "CRITICAL"


def test_needs_attention_inside_warn_window():
    created = dt.datetime(2026, 6, 30, tzinfo=UTC)
    st = status_from_payload(_payload(created), created + dt.timedelta(days=5, hours=12))
    ok, level, _ = needs_attention(st, warn_within_days=2.0)  # 1.5 days left
    assert ok is True
    assert level == "WARNING"


def test_no_attention_when_healthy():
    created = dt.datetime(2026, 6, 30, tzinfo=UTC)
    st = status_from_payload(_payload(created), created + dt.timedelta(days=1))
    ok, _, _ = needs_attention(st, warn_within_days=2.0)  # 6 days left
    assert ok is False


def test_needs_attention_when_token_absent():
    st = status_from_payload(None, dt.datetime.now(UTC))
    ok, level, _ = needs_attention(st, warn_within_days=2.0)
    assert ok is True
    assert level == "CRITICAL"


# ── file reading ────────────────────────────────────────────────────────

def test_read_token_status_from_file(tmp_path):
    created = dt.datetime(2026, 7, 1, tzinfo=UTC)
    f = tmp_path / "schwab_token.json"
    f.write_text(json.dumps(_payload(created)))
    st = read_token_status(f, now=created + dt.timedelta(days=2))
    assert st["present"] is True
    assert st["days_remaining"] == pytest.approx(5.0, abs=1e-6)


def test_read_token_status_missing_file(tmp_path):
    st = read_token_status(tmp_path / "nope.json", now=dt.datetime.now(UTC))
    assert st["present"] is False
    assert st["expired"] is True


def test_read_token_status_corrupt_file(tmp_path):
    f = tmp_path / "schwab_token.json"
    f.write_text("{not json")
    st = read_token_status(f, now=dt.datetime.now(UTC))
    assert st["present"] is False
