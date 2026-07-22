#!/usr/bin/env bash
# Daily breakout_52w paper forward-tester (Trading Lab).
#
# Invoked by cron on the deploy host (weekdays 14:30 server time):
#   30 14 * * 1-5 /opt/sgt-schwab/run_paper_forward.sh >> /opt/sgt-schwab/state/paper_forward.log 2>&1
#
# Steps the JSON ledger via lab PaperRunner (SimFill only — never a real order).
# Experiment: breakout_52w_paper in config/experiments.yaml
# Ledger: state/experiments/breakout_52w_paper/ledger.json (migrates legacy if needed)
#
# NOTE: lives at the repo root so deploy-remote.sh's `rsync --delete` preserves
# it. The universe + ledger live under state/ (rsync-excluded, podman-mounted).
set -uo pipefail

echo "===== $(date '+%Y-%m-%d %H:%M %Z') ====="

if ! podman exec -w /app sgt-schwab-bot \
    python -m scripts.lab.run_experiment --id breakout_52w_paper
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

# Market education brief (conditions + playbook) for dashboard / agent
podman exec -w /app sgt-schwab-bot \
    python -m scripts.lab.market_brief 2>/dev/null || true

# Staleness is also checked on token_watch; optional post-run scoreboard line
podman exec -w /app sgt-schwab-bot \
    python -m scripts.lab.scoreboard --id breakout_52w_paper 2>/dev/null || true
