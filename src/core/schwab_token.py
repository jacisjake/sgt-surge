"""Schwab refresh-token expiry tracking.

Schwab refresh tokens live exactly 7 days from creation and CANNOT be renewed by
any API call — only a fresh OAuth consent (a human logging in to Schwab) mints a
new one. schwab-py auto-refreshes the 30-minute *access* token using the refresh
token, but when the refresh token dies the client goes unauthenticated and stays
that way until someone re-authorizes.

So the goal here is not to prevent expiry (impossible) but to never be surprised
by it: schwab-py records `creation_timestamp` next to the token, which is all we
need to know exactly when the refresh token dies.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Optional

UTC = dt.timezone.utc

#: Schwab's fixed refresh-token lifetime. Not configurable by us — it's their rule.
REFRESH_TOKEN_LIFETIME = dt.timedelta(days=7)


def status_from_payload(payload: Optional[dict[str, Any]], now: dt.datetime) -> dict:
    """Derive refresh-token expiry state from a schwab-py token payload.

    Pure: no I/O, `now` injected. A payload without `creation_timestamp` is
    treated as absent (and therefore expired) rather than guessed at.
    """
    if not payload or "creation_timestamp" not in payload:
        return {
            "present": False,
            "created_at": None,
            "expires_at": None,
            "seconds_remaining": None,
            "days_remaining": None,
            "expired": True,
        }

    created = dt.datetime.fromtimestamp(payload["creation_timestamp"], tz=UTC)
    expires = created + REFRESH_TOKEN_LIFETIME
    remaining = (expires - now).total_seconds()
    return {
        "present": True,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "seconds_remaining": remaining,
        "days_remaining": remaining / 86400.0,
        "expired": remaining <= 0,
    }


def read_token_status(token_path, now: Optional[dt.datetime] = None) -> dict:
    """Read the token file and report expiry state. Missing/corrupt reads as absent."""
    now = now or dt.datetime.now(UTC)
    path = Path(token_path)
    if not path.exists():
        return status_from_payload(None, now)
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return status_from_payload(None, now)
    return status_from_payload(payload, now)


def needs_attention(status: dict, warn_within_days: float = 2.0) -> tuple[bool, str, str]:
    """Should we alert, at what level, and with what message?

    Returns (needs_alert, level, message). CRITICAL once the token is dead (or
    missing) — the bot is down. WARNING while it still works but dies soon, which
    is the window we actually want to act in.
    """
    if not status["present"]:
        return True, "CRITICAL", "No Schwab token found — the bot cannot authenticate."

    if status["expired"]:
        return (
            True,
            "CRITICAL",
            f"Schwab refresh token EXPIRED at {status['expires_at']} "
            f"({abs(status['days_remaining']):.1f} days ago). The bot is down and the "
            f"lab live runner is failing.",
        )

    if status["days_remaining"] <= warn_within_days:
        hours = status["seconds_remaining"] / 3600.0
        return (
            True,
            "WARNING",
            f"Schwab refresh token expires in {hours:.0f}h "
            f"(at {status['expires_at']}). Re-authorize before it dies.",
        )

    return False, "OK", f"Token healthy — {status['days_remaining']:.1f} days remaining."
