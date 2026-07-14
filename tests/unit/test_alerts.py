"""Tests for email alerting."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.bot.alerts import alerts_configured, send_email_alert


def _cfg(**over):
    base = dict(
        alert_email_to="jac@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        smtp_from="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_alerts_configured_when_all_present():
    assert alerts_configured(_cfg()) is True


def test_alerts_not_configured_when_missing_host():
    assert alerts_configured(_cfg(smtp_host="")) is False


def test_alerts_not_configured_when_missing_recipient():
    assert alerts_configured(_cfg(alert_email_to="")) is False


def test_alerts_not_configured_when_missing_password():
    assert alerts_configured(_cfg(smtp_password="")) is False


def test_send_noops_when_unconfigured():
    """An unconfigured alert channel must never raise — the bot keeps running."""
    assert send_email_alert("subj", "body", _cfg(smtp_host="")) is False


@patch("src.bot.alerts.smtplib.SMTP")
def test_send_uses_starttls_and_login(mock_smtp):
    server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = server

    assert send_email_alert("subj", "body", _cfg()) is True

    mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=20)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("bot@example.com", "secret")
    server.send_message.assert_called_once()

    msg = server.send_message.call_args[0][0]
    assert msg["Subject"] == "subj"
    assert msg["To"] == "jac@example.com"
    assert msg["From"] == "bot@example.com"  # falls back to smtp_user


@patch("src.bot.alerts.smtplib.SMTP")
def test_send_honors_explicit_from(mock_smtp):
    mock_smtp.return_value.__enter__.return_value = MagicMock()
    send_email_alert("s", "b", _cfg(smtp_from="alerts@example.com"))
    msg = mock_smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
    assert msg["From"] == "alerts@example.com"


@patch("src.bot.alerts.smtplib.SMTP", side_effect=OSError("connection refused"))
def test_send_swallows_smtp_errors(_mock_smtp):
    """A dead SMTP server must not take the bot down with it."""
    assert send_email_alert("subj", "body", _cfg()) is False
