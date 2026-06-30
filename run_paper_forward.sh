#!/usr/bin/env bash
# Daily breakout_52w paper forward-tester runner.
#
# Invoked by cron on the deploy host (weekdays 14:30 server time):
#   30 14 * * 1-5 /opt/sgt-schwab/run_paper_forward.sh >> /opt/sgt-schwab/state/paper_forward.log 2>&1
#
# Steps the JSON ledger forward by one trading day inside the bot container,
# which holds the Schwab token + deps. Simulated fills only — never a real order.
#
# NOTE: lives at the repo root so deploy-remote.sh's `rsync --delete` preserves
# it. The universe + ledger live under state/ (rsync-excluded, podman-mounted).
set -euo pipefail

echo "===== $(date '+%Y-%m-%d %H:%M %Z') ====="

podman exec -w /app sgt-schwab-bot \
    python -m scripts.research.swing.paper_forward \
    --symbols-file /app/state/breakout_universe.txt \
    --state-file state/swing_paper_breakout.json
