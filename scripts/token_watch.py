"""Schwab token watchdog — warn BEFORE the refresh token dies.

Schwab refresh tokens expire exactly 7 days after creation and cannot be renewed
programmatically; only a human OAuth re-consent mints a new one. There is no code
that avoids that. What we can do is never be surprised by it.

Run daily from cron. Silent (exit 0, no mail) while the token is healthy; emails
a WARNING inside the warn window, and a CRITICAL once it's dead.

    Cron (server):  0 8 * * *  /opt/sgt-schwab/run_token_watch.sh

    --force  send the status email even when healthy (use to verify SMTP works)
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

from src.bot.alerts import alerts_configured, send_email_alert  # noqa: E402
from src.bot.config import get_bot_config  # noqa: E402
from src.core.schwab_token import needs_attention, read_token_status  # noqa: E402

REAUTH_URL = "https://ut.gitsum.rest"


def build_body(level: str, message: str, status: dict) -> str:
    lines = [
        message,
        "",
        f"  token present   : {status['present']}",
        f"  created at      : {status['created_at']}",
        f"  expires at      : {status['expires_at']}",
    ]
    if status["days_remaining"] is not None:
        lines.append(f"  days remaining  : {status['days_remaining']:.2f}")
    if level != "OK":
        lines += [
            "",
            f"ACTION: open {REAUTH_URL} and click \"Authorize Schwab\" to re-authorize.",
            "",
            "Schwab refresh tokens expire 7 days after creation and cannot be renewed",
            "by any API call — this re-consent is manual by design, not a bug in the bot.",
            "",
            "While the token is dead the bot cannot trade and the breakout_52w",
            "paper-forward cron fails, so the ledger stops advancing.",
        ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="email the status even when the token is healthy")
    args = ap.parse_args()

    cfg = get_bot_config()
    status = read_token_status(cfg.schwab_token_path)
    alert, level, message = needs_attention(status, cfg.alert_warn_within_days)

    print(f"[{level}] {message}")

    if not alert and not args.force:
        return 0

    if not alerts_configured(cfg):
        print("SMTP not configured — set ALERT_EMAIL_TO / SMTP_* in .env to receive alerts.",
              file=sys.stderr)
        return 1

    subject = f"[sgt-schwab] {level}: Schwab token"
    if level == "CRITICAL":
        subject = "[sgt-schwab] CRITICAL: Schwab token expired — bot is down"
    elif level == "WARNING":
        hours = status["seconds_remaining"] / 3600.0
        subject = f"[sgt-schwab] Schwab token expires in {hours:.0f}h — re-auth needed"

    sent = send_email_alert(subject, build_body(level, message, status), cfg)

    # Also alert if the primary paper ledger stopped advancing (cron silence).
    try:
        from src.lab.metrics.daily_equity import check_experiment_staleness
        from src.lab.registry import load_registry

        reg = load_registry()
        if "breakout_52w_paper" in reg:
            stale = check_experiment_staleness(reg["breakout_52w_paper"], max_sessions=3)
            if stale.get("stale"):
                print(f"[STALE] paper ledger: {stale.get('reason')}")
                if alerts_configured(cfg):
                    send_email_alert(
                        "[sgt-schwab] WARNING: paper ledger stale — cron may be dead",
                        (
                            f"breakout_52w_paper last_date={stale.get('last_date')}\n"
                            f"as_of={stale.get('as_of')}\n"
                            f"weekdays_since={stale.get('weekdays_since')}\n"
                            f"ledger={stale.get('ledger_path')}\n\n"
                            "Check run_paper_forward.sh cron and Schwab token.\n"
                        ),
                        cfg,
                    )
    except Exception as e:  # noqa: BLE001
        print(f"[STALE] check skipped: {e}", file=sys.stderr)

    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
