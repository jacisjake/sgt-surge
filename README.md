# Sgt Schwab

> **DISCLAIMER: This project is for educational and research purposes only. It is not financial advice. Trading stocks involves substantial risk of loss. Past performance is not indicative of future results. You could lose some or all of your invested capital. Do not trade with money you cannot afford to lose. By using this software, you acknowledge that you are solely responsible for your own trading decisions and any resulting financial outcomes.**

Algorithmic momentum day-trading bot for Charles Schwab. Built for small accounts with aggressive risk management.

## Strategy

**Opening Range Breakout (ORB)** -- long-only breakout trading on 5-minute bars.

- **Scanner**: TradingView screener for pre-market gappers (top 5, configurable), enriched with relative volume and float data
- **Opening range**: 09:30-09:45 ET window; the OR high/low is locked at 09:45 ET
- **Entry**: single 5-min close above the OR high, with a volume filter (long-only)
- **Stop**: OR low. **Target**: entry + 2R, with progressive R-trailing (breakeven floor at +1R, chandelier overlay above)
- **Schedule**: scanning during the RTH session, safety-net flatten at 15:55 ET (no overnight)
- **Position sizing**: hybrid (~90% of buying power deploy, capped by max risk %)
- **Max trades/day**: cash-account-constrained (no fixed cap)

Bars: Schwab streams 1-min bars, aggregated internally to 5-min. The live strategy
lives in `src/bot/signals/orb.py`.

### Research track

A separate research effort (`scripts/research/`, `docs/superpowers/`) backtests
structurally different edges to eventually replace ORB. The current front-runner,
`breakout_52w` (52-week-high swing momentum), runs as a **dry-run paper
forward-tester** (`run_paper_forward.sh`, weekday cron) alongside the live bot for
head-to-head comparison via the `/api/compare` dashboard. See
`docs/superpowers/results/2026-06-11-strategy-bakeoff.md`.

## Prerequisites

- **Python 3.11+**
- **Charles Schwab account** -- [Sign up here](https://www.schwab.com/)
- **Schwab OAuth app** -- Create one in Schwab's developer portal
- **Financial Modeling Prep API key** (free tier, optional) -- for float data enrichment. [Get one here](https://financialmodelingprep.com/developer/docs/)
- **Podman** or **Docker** (for containerized deployment, optional for local dev)

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/jacisjake/sgt-schwab.git
cd sgt-schwab

python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your credentials. See the comments in `.env.example` for guidance on each variable.

**Required:**
- `SCHWAB_APP_KEY` -- OAuth app key from Schwab developer portal
- `SCHWAB_APP_SECRET` -- OAuth app secret
- `TRADING_MODE` -- `dry_run` (simulated fills) or `live` (real money). No `paper`.

**Optional but recommended:**
- `ENABLE_ORB_LIVE` -- default `false`; must be `true` **and** `TRADING_MODE=live` for real ORB broker orders
- `FMP_API_KEY` -- enables float filtering (free tier: 250 requests/day)
- `SCHWAB_ACCOUNT_HASH` -- pin a specific account hash after first OAuth

### 3. Set up Charles Schwab OAuth

1. Go to Schwab's developer portal and create an OAuth application
2. Note your **Client ID** and **Client Secret**
3. Register the redirect URI in the developer portal as `https://ut.gitsum.rest/schwab/oauth/callback` (or `http://localhost:8080/schwab/oauth/callback` for local dev)
4. Add `SCHWAB_CLIENT_ID` and `SCHWAB_CLIENT_SECRET` to your `.env` file
5. On first run, visit `http://localhost:8080` and click "Authorize Schwab" to complete the OAuth flow

### 4. Run the bot

```bash
python scripts/run_bot.py
```

The web dashboard will be available at **http://localhost:8080**.

**CLI options:**

```bash
python scripts/run_bot.py --dry-run       # Show config and exit
python scripts/run_bot.py --status        # Show account status
python scripts/run_bot.py --check-signals # Check for signals once and exit
```

## Project Structure

```
sgt-schwab/
├── config/
│   └── settings.py              # Pydantic settings (env var validation)
├── src/
│   ├── bot/
│   │   ├── main.py              # TradingBot orchestrator
│   │   ├── web.py               # FastAPI dashboard + API server
│   │   ├── config.py            # Bot-specific config (strategy params)
│   │   ├── executor.py          # Signal -> order execution
│   │   ├── processor.py         # Signal filtering and validation
│   │   ├── scheduler.py         # APScheduler job management
│   │   ├── screener.py          # Stock screener logic
│   │   ├── tradingview_screener.py  # TradingView API integration
│   │   ├── stream_handler.py    # 1-min -> 5-min bar aggregation
│   │   ├── monitor.py           # Position monitoring and P&L
│   │   ├── comparison.py        # Live-vs-paper strategy stats (/api/compare)
│   │   ├── float_provider.py    # Float data from FMP API
│   │   ├── signals/             # Signal generation strategies
│   │   │   ├── base.py          # Abstract signal base class
│   │   │   └── orb.py           # Opening Range Breakout (live strategy)
│   │   └── state/               # State persistence
│   │       ├── persistence.py   # Bot state file I/O
│   │       └── trade_ledger.py  # Trade history tracking
│   ├── core/                    # Schwab broker integration
│   │   ├── schwab_client.py     # Schwab REST API wrapper (schwab-py)
│   │   ├── schwab_stream.py     # Schwab streaming (1-min bars/quotes)
│   │   ├── order_executor.py    # Order submission
│   │   ├── position_manager.py  # Position tracking
│   │   └── market_calendar.py   # Trading-session calendar
│   ├── risk/                    # Risk management
│   │   ├── portfolio_limits.py  # Portfolio-level limits
│   │   ├── position_sizer.py    # Position sizing
│   │   └── stop_manager.py      # Stop-loss and trailing stops
│   └── data/
│       └── indicators.py        # Technical indicators
├── scripts/
│   ├── run_bot.py               # Main entry point (container CMD)
│   ├── healthcheck.sh           # Remote health monitoring
│   ├── smoke_schwab.py          # Schwab auth/connectivity smoke test
│   ├── backtest_orb.py          # Legacy ORB research backtest
│   └── research/                # Strategy bake-off + swing paper-forward harness
├── deploy/
│   ├── podman-compose.yml       # Container orchestration
│   └── deploy-remote.sh         # Remote deployment script
├── docs/superpowers/            # Plans, specs, bake-off results, lab design
├── run_paper_forward.sh         # Daily breakout_52w paper forward-tester (cron)
├── tests/
│   └── unit/                    # Unit tests
├── state/                       # Runtime state (not tracked)
└── logs/                        # Application logs (not tracked)
```

## Configuration Reference

All configuration is done through environment variables (or `.env` file). See `.env.example` for the full list.

### Authentication

| Variable | Required | Description |
|----------|----------|-------------|
| `SCHWAB_APP_KEY` | Yes | OAuth app key from Schwab developer portal |
| `SCHWAB_APP_SECRET` | Yes | OAuth app secret |
| `SCHWAB_ACCOUNT_HASH` | Optional | Pin a specific Schwab account hash |

### Trading

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADING_MODE` | `dry_run` | `dry_run` (simulated fills) or `live` (real money). No `paper`. |
| `ENABLE_ORB_LIVE` | `false` | When `false`, ORB path will not place real broker orders even if `TRADING_MODE=live` |
| `ENABLE_EXTENDED_HOURS` | `true` | Allow extended hours trading |

### Trading Lab cutover (capital safety)

Design: [`docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`](docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md).

Operator checklist for server cutover:

1. Set server `.env` to `TRADING_MODE=dry_run` (leave `ENABLE_ORB_LIVE` false/unset).
2. Restart only the `sgt-schwab-bot` container.
3. Verify `/api/status` reports `"trading_mode":"dry_run"`. Healthcheck `"mode":"running"` means authenticated, **not** that ORB is trading live.
4. Keep Schwab token refresh and the `run_paper_forward.sh` weekday paper cron.
5. Only set `ENABLE_ORB_LIVE=true` if intentionally re-enabling ORB live money.

### Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITION_RISK_PCT` | `0.02` | Max risk per trade (2% of account) |
| `MAX_PORTFOLIO_RISK_PCT` | `0.10` | Max total portfolio risk (10%) |
| `MAX_POSITIONS` | `5` | Max concurrent open positions |
| `MAX_DRAWDOWN_PCT` | `0.15` | Max drawdown before halt (15%) |

### Data & Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `FMP_API_KEY` | *(none)* | Financial Modeling Prep API key for float data |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

## Deployment

### Local (no container)

Just run `python scripts/run_bot.py`. The bot runs in the foreground.

### Container (Podman or Docker)

Build and run locally:

```bash
# Podman
cd deploy
podman-compose up -d --build

# Docker
cd deploy
docker compose up -d --build
```

The container mounts persistent volumes for `state/` and `logs/`, exposes port 8080, and restarts automatically.

### Remote Server

Deploy to a remote Linux server with Podman:

```bash
cd deploy && ./deploy-remote.sh jacisjake@ut.gitsum.rest --build
```

**What the script does:**
1. Creates `/opt/sgt-schwab/` on the remote server
2. Syncs project files via rsync (excludes venv, .git, logs, state, .env)
3. Copies your local `.env` to the server on first deploy only (never overwrites)
4. Builds the container image if `--build` is passed or no image exists
5. Starts the container with `podman-compose up -d`

**Schwab OAuth Setup**

The Schwab OAuth callback is path-scoped: register `https://ut.gitsum.rest/schwab/oauth/callback` in the Schwab developer portal. No DNS or Caddy changes are required — the existing `ut.gitsum.rest` block proxies `/schwab/oauth/*` to the bot on port 8080.

The first run will boot the bot in setup mode (no token yet). Visit `https://ut.gitsum.rest` and click "Authorize Schwab" to complete the OAuth flow; the bot will write `state/schwab_token.json` and refresh it automatically.

**After first deploy**, edit the `.env` on the server directly:

```bash
ssh jacisjake@ut.gitsum.rest
nano /opt/sgt-schwab/.env
```

**Process management:** production runs under Podman (`restart: always` in `podman-compose.yml`). There is no checked-in systemd unit or launchd plist; use `deploy-remote.sh` and `podman-compose`.

**Optional: reverse proxy with Caddy** -- if you want HTTPS access to the dashboard:

```
# Add to /etc/caddy/Caddyfile on the server
your-domain.com {
    reverse_proxy localhost:8080
}
```

Then reload: `sudo systemctl reload caddy`

### Monitoring

Check bot health on a remote server:

```bash
# Quick status
ssh jacisjake@ut.gitsum.rest 'podman ps --filter name=sgt-schwab'

# Tail logs
ssh jacisjake@ut.gitsum.rest 'podman logs -f sgt-schwab-bot'

# Or use the included health check script (edit HOST inside first)
bash scripts/healthcheck.sh
```

The dashboard at port 8080 also shows live status, positions, signals, and trade history.

## Testing

```bash
# Run all tests
pytest tests/unit/ -v

# Run specific test file
pytest tests/unit/test_position_sizer.py -v

# Run with coverage
pytest tests/unit/ --cov=src
```

## Backtesting

Legacy ORB research backtest:

```bash
# Backtest the live ORB strategy (chandelier stops, daily snapshots)
python scripts/backtest_orb.py
```

The broader strategy bake-off (structurally different edges, OOS/regime kill-tests,
swing paper-forward) lives under `scripts/research/`; see
`docs/superpowers/results/2026-06-11-strategy-bakeoff.md` for findings.

## Architecture

The bot runs as a single async process with these components:

1. **Scheduler** (APScheduler) -- triggers scanner, monitor, and sync jobs on intervals
2. **Scanner** (TradingView + FMP) -- finds candidate pre-market gappers matching the criteria
3. **Streaming** (Schwab) -- streams real-time 1-min bars/quotes for watchlist symbols, aggregated internally to 5-min
4. **Signal Generator** -- evaluates bars against the ORB rules, emits buy/sell signals
5. **Executor** -- converts signals to broker orders with position sizing and risk checks
6. **Position Monitor** -- tracks open positions, manages trailing stops, triggers exits
7. **Risk Manager** -- enforces per-trade, portfolio, and daily loss limits
8. **Dashboard** (FastAPI) -- web UI for monitoring, manual controls, trade history, and OAuth flow

## License

MIT
