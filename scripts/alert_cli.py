"""Send an ad-hoc email alert from a shell script.

    python -m scripts.alert_cli "SUBJECT" "BODY"

Used by cron wrappers so a failed run shouts instead of quietly
writing a traceback into a log nobody reads. Exits non-zero if the mail could
not be sent (unconfigured SMTP or a send failure), so cron surfaces it.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from src.bot.alerts import send_email_alert  # noqa: E402
from src.bot.config import get_bot_config  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    subject, body = sys.argv[1], sys.argv[2]
    return 0 if send_email_alert(subject, body, get_bot_config()) else 1


if __name__ == "__main__":
    sys.exit(main())
