# Project Context for Claude

## Deployment Environment

- **Remote server**: `jacisjake@ut.gitsum.rest`
- **Web server**: Caddy (reverse proxy)
- **Container runtime**: Podman (not Docker)
- **Deploy command**: `cd deploy && ./deploy-remote.sh jacisjake@ut.gitsum.rest --build`
- **Bot runs on port**: 8080 (internal)
- **Public URL**: https://ut.gitsum.rest (via Caddy reverse proxy)

## Caddy Configuration

To add a new site, edit `/etc/caddy/Caddyfile` on the server and reload:
```
sudo systemctl reload caddy
```

## Key Directories on Server

- `/opt/sgt-schwab/` - Application files
- `/opt/sgt-schwab/.env` - Environment variables (Schwab API keys)
- Container volumes for state/logs

## Trading Context

- **Broker**: Charles Schwab API (schwab-py). Migration off tastytrade is **complete** — the live bot on `ut.gitsum.rest` runs Schwab and is authenticated (`src/core/schwab_client.py`, `schwab_stream.py`). Token auto-refreshes (`state/schwab_token.json`).
- **Starting capital**: ~$270
- **Goal**: $25,000
- **Live strategy (deployed)**: Opening Range Breakout (ORB)
  - Timeframe: 5-min bars (Schwab streams 1-min, aggregated internally)
  - Symbol universe: pre-market gappers, top 5 via TradingView (configurable)
  - Opening-range window: 9:30–9:45 ET
  - Entry rule: long-only; single 5-min close above OR high with volume filter
  - Stop: OR low. Target: entry + 2R, with progressive R-trailing (breakeven floor at +1R, chandelier overlay above)
  - Max trades/day: cash-account-constrained (no fixed cap)
  - Position sizing: hybrid (~90% BP deploy, capped by risk %)
- **Trading mode**: `TRADING_MODE` is `dry_run` | `live` only (no `paper`). Default is `dry_run` (simulated fills). Real ORB broker submits also require `ENABLE_ORB_LIVE=true` (default **false**).
- **Migration design**: see `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`
- **Trading Lab v1 design**: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`

## Strategy Switch — Current State (as of 2026-07-22)

Active effort: capital safety for idle ORB + Trading Lab v1 cutover. Tracks:

- **ORB** — stream/scan may still run; real money path gated by `ENABLE_ORB_LIVE=false` (default) and ops should set server `TRADING_MODE=dry_run`. Last real signal 2026-06-04; account flat ~$199.
- **`breakout_52w`** (52-week-high swing momentum) — bake-off winner (+55% backtest), **dry-run paper forward-tester** via `run_paper_forward.sh` (weekday cron). Not yet promoted to live.
- **`runner_momentum`** — **spec only** (`docs/superpowers/specs/2026-07-01-runner-momentum-backtest-design.md`); nothing implemented.

Decision gate: do NOT promote a strategy to live until it proves out in forward test.
Bake-off findings: `docs/superpowers/results/2026-06-11-strategy-bakeoff.md`.

## Lab cutover checklist (server capital safety)

Before/with first Trading Lab deploy on `ut.gitsum.rest`:

1. Set `/opt/sgt-schwab/.env` → `TRADING_MODE=dry_run` (and leave `ENABLE_ORB_LIVE` unset/false).
2. Restart **only** `sgt-schwab-bot` (never `podman stop -a`).
3. Verify `curl -s http://localhost:8080/api/status` shows `"trading_mode":"dry_run"`. Note: `"mode":"running"` is **auth-based**, not proof of ORB live trading.
4. Keep Schwab token refresh (`state/schwab_token.json`) and `run_paper_forward.sh` cron; healthcheck is auth-based until rewritten.
5. Do **not** set `ENABLE_ORB_LIVE=true` unless intentionally re-enabling ORB live money.
