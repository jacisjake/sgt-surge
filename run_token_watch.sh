#!/usr/bin/env bash
# Daily Schwab token watchdog.
#
# Invoked by cron on the deploy host (daily 08:00 server time):
#   0 8 * * * /opt/sgt-schwab/run_token_watch.sh >> /opt/sgt-schwab/state/token_watch.log 2>&1
#
# Silent while the refresh token is healthy; emails a WARNING inside the warn
# window (default 2 days out) and a CRITICAL once it has expired.
#
# Schwab refresh tokens expire 7 days after creation and CANNOT be renewed by an
# API call — re-consent is manual. This job exists so that expiry is never a
# surprise, not to prevent it.
#
# NOTE: lives at the repo root so deploy-remote.sh's `rsync --delete` preserves
# it.
set -euo pipefail

echo "===== $(date '+%Y-%m-%d %H:%M %Z') ====="

podman exec -w /app sgt-schwab-bot python -m scripts.token_watch "$@"
