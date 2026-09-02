#!/bin/bash
# Health check for Sgt Schwab bot on ut.gitsum.rest
#
# Usage:
#   bash scripts/healthcheck.sh
#   REMOTE_HOST=jacisjake@ut.gitsum.rest bash scripts/healthcheck.sh
#
# Notes:
#   - Public /api/status "mode":"running" means authenticated + account fetch OK,
#     not that ORB is placing live orders.
#   - Prefer trading_mode from the same endpoint for capital-safety checks.

set -euo pipefail

HOST="${REMOTE_HOST:-jacisjake@ut.gitsum.rest}"
PUBLIC_URL="${PUBLIC_URL:-https://ut.gitsum.rest}"
CONTAINER="${CONTAINER_NAME:-sgt-schwab-bot}"

echo "=== Sgt Schwab Health Check ==="
echo "Host:      $HOST"
echo "Public:    $PUBLIC_URL"
echo "Container: $CONTAINER"
echo ""

# Public API status (auth-based health + trading_mode)
echo "API /api/status:"
if STATUS_JSON="$(curl -sf --max-time 10 "${PUBLIC_URL}/api/status" 2>/dev/null)"; then
  echo "  $STATUS_JSON" | head -c 2000
  echo ""
  if echo "$STATUS_JSON" | grep -qE '"mode"[[:space:]]*:[[:space:]]*"running"'; then
    echo "  mode: running (authenticated)"
  else
    echo "  mode: not running (setup/error or unexpected body)"
  fi
  if echo "$STATUS_JSON" | grep -qE '"trading_mode"[[:space:]]*:[[:space:]]*"dry_run"'; then
    echo "  trading_mode: dry_run"
  elif echo "$STATUS_JSON" | grep -qE '"trading_mode"[[:space:]]*:[[:space:]]*"live"'; then
    echo "  trading_mode: live"
  fi
else
  echo "  FAILED: could not reach ${PUBLIC_URL}/api/status"
fi

echo ""
echo "Container:"
ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" \
  "podman ps --filter name=${CONTAINER} --format '  {{.Names}}\t{{.Status}}\t{{.Ports}}'" 2>/dev/null \
  || echo "  FAILED: ssh/podman ps (check host or keys)"

echo ""
echo "Recent logs (errors / orders / signals):"
ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" \
  "podman logs --tail 80 ${CONTAINER} 2>&1 | grep -E '(ERROR|Error|Signal|Exit|Filled|order|ORB|breakout)' | tail -10" 2>/dev/null \
  || echo "  (no matching lines or ssh/podman failed)"

echo ""
echo "Done."
