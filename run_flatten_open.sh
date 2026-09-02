#!/usr/bin/env bash
# One-shot flatten of the live mega-cap book at the cash-session open.
#
# Intended crontab (America/Denver on ut.gitsum.rest = 9:30 ET):
#   30 7 18 8 * /opt/sgt-schwab/run_flatten_open.sh >> /opt/sgt-schwab/state/flatten_open.log 2>&1
set -uo pipefail

echo "===== $(date '+%Y-%m-%d %H:%M %Z') flatten ====="

if ! podman exec -w /app sgt-schwab-bot \
    python -m scripts.flatten_positions --live
then
    echo "!!! flatten FAILED — sending alert"
    podman exec -w /app sgt-schwab-bot python -m scripts.alert_cli \
        "[sgt-schwab] flatten FAILED — mega-cap book still open" \
        "The 9:30 ET flatten of the live swing book failed.

Check /opt/sgt-schwab/state/flatten_open.log and Schwab token.
Do not let live_swing buy into the old book on top of a partial flatten." || true
    exit 1
fi
