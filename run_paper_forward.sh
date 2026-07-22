#!/usr/bin/env bash
# Daily breakout_52w paper forward-tester runner.
#
# Invoked by cron on the deploy host (weekdays 14:30 server time):
#   30 14 * * 1-5 /opt/sgt-schwab/run_paper_forward.sh >> /opt/sgt-schwab/state/paper_forward.log 2>&1
#
# Steps the JSON ledger forward by one trading day inside the bot container,
# which holds the Schwab token + deps. Simulated fills only — never a real order.
# Engine: scripts.research.swing.paper_forward → src.lab Breakout52wStrategy + SimFill.
# Lab CLI alternative: python -m scripts.lab.run_experiment --id breakout_52w_paper
#
# NOTE: lives at the repo root so deploy-remote.sh's `rsync --delete` preserves
# it. The universe + ledger live under state/ (rsync-excluded, podman-mounted).
set -uo pipefail

echo "===== $(date '+%Y-%m-%d %H:%M %Z') ====="

if ! podman exec -w /app sgt-schwab-bot \
    python -m scripts.research.swing.paper_forward \
    --symbols-file /app/state/breakout_universe.txt \
    --state-file state/swing_paper_breakout.json
then
    # Don't fail silently: a dead run means the ledger stops advancing, and the
    # usual cause is an expired Schwab refresh token (7-day lifetime).
    echo "!!! paper-forward run FAILED — sending alert"
    podman exec -w /app sgt-schwab-bot python -m scripts.alert_cli \
        "[sgt-schwab] paper-forward FAILED — ledger not advancing" \
        "The daily breakout_52w paper-forward run failed.

Most likely cause: the Schwab refresh token expired (they die 7 days after
creation and cannot be renewed via API).

Fix: open https://ut.gitsum.rest and click \"Authorize Schwab\".

Log: /opt/sgt-schwab/state/paper_forward.log" || true
    exit 1
fi
