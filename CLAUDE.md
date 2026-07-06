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
- **Trading mode**: `TRADING_MODE=dry_run` (simulated fills) is the default. The **deployed bot runs `live`** (real orders) on a ~$199 cash account.
- **Migration design**: see `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`

## Strategy Switch — Current State (as of 2026-07-06)

The active effort is retiring the idle live ORB for a validated edge. Three tracks:

- **ORB** — LIVE and healthy but effectively idle (last real signal 2026-06-04); flat ~$198.98, no positions.
- **`breakout_52w`** (52-week-high swing momentum) — the validated bake-off winner (+55% backtest), running as a **dry-run paper forward-tester** via `run_paper_forward.sh` (weekday cron on the server). **Currently underwater in forward test (~$191.6 vs $200 start, ≈−4.2%, 8 open positions, last stepped 2026-07-02)** — not yet promoted to live.
- **`runner_momentum`** (intraday coil-break on small-cap runners) — **spec only** (`docs/superpowers/specs/2026-07-01-runner-momentum-backtest-design.md`); backtest is Sub-project 1 (needs an Alpaca account); nothing implemented.

Decision gate: do NOT promote a strategy to live until it proves out in forward test.
Bake-off findings: `docs/superpowers/results/2026-06-11-strategy-bakeoff.md`.
