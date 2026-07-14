"""Email alerting over stdlib SMTP.

No new dependency and no local MTA required — the server has no mail agent, but
outbound 587 is open, so we talk STARTTLS directly to an external SMTP provider.

Credentials come from env (see BotConfig). If SMTP isn't configured the sender
no-ops with a warning: an alert channel going missing must never be able to take
the trading bot down.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from loguru import logger


def alerts_configured(cfg) -> bool:
    """True when enough SMTP settings are present to actually send."""
    return bool(
        getattr(cfg, "smtp_host", "")
        and getattr(cfg, "smtp_user", "")
        and getattr(cfg, "smtp_password", "")
        and getattr(cfg, "alert_email_to", "")
    )


def send_email_alert(subject: str, body: str, cfg) -> bool:
    """Send one alert email. Returns True on success; never raises."""
    if not alerts_configured(cfg):
        logger.warning("[ALERT] SMTP not configured — dropping alert: {}", subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = cfg.alert_email_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(msg)
    except Exception as e:  # noqa: BLE001 — alerting must never crash the caller
        logger.error("[ALERT] send failed ({}): {}", type(e).__name__, e)
        return False

    logger.info("[ALERT] sent: {}", subject)
    return True
