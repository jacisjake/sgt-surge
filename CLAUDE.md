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

- **Broker**: Charles Schwab API (port from tastytrade in progress — current code on disk is still tastytrade)
- **Starting capital**: ~$270
- **Goal**: $25,000
- **Strategy**: Opening Range Breakout (ORB)
  - Timeframe: 5-min bars (Schwab streams 1-min, aggregated internally)
  - Symbol universe: pre-market gappers, top 5 via TradingView (configurable)
  - Opening-range window: 9:30–9:45 ET
  - Entry rule: long-only; single 5-min close above OR high with volume filter
  - Stop: OR low. Target: entry + 2R, with progressive R-trailing (breakeven floor at +1R, chandelier overlay above)
  - Max trades/day: cash-account-constrained (no fixed cap)
  - Position sizing: hybrid (~90% BP deploy, capped by risk %)
- **Trading mode**: `TRADING_MODE=dry_run` (simulated fills) is the default. Set `live` to send real orders.
- **Migration design**: see `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`
