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

- `/opt/sgt-surge/` - Application files
- `/opt/sgt-surge/.env` - Environment variables (Alpaca keys)
- Container volumes for state/logs

## Trading Context

- **Broker**: tastytrade (live trading enabled)
- **Starting capital**: $250
- **Goal**: $25,000
- **Strategy**: Ross Cameron-style momentum surge on low-float stocks
- **Timeframe**: 5-min bars
- **Target stocks**: $1-$10 price (prefer $2+), min 500K float, 10%+ daily change, 5x+ relative volume
- **Schedule**: Scanning 6:00 AM - 4:00 PM ET, safety net close at 3:55 PM ET
- **No trading window**: Entries allowed anytime during scanning hours
- **Max trades/day**: 10
- **Position sizing**: Up to 90% of buying power, risk-constrained (2% max risk)
- **Scanner**: TradingView screener, enriched with relative volume + float data
- **Float data**: Financial Modeling Prep (FMP) free API
- **Entry**: Momentum surge, price > VWAP
- **Exit**: VWAP breakdown (2 consecutive closes below), progressive R-trailing stop, or safety net (3:55 PM)
