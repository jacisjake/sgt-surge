# Sgt Schwab

> **DISCLAIMER: This project is for educational and research purposes only. It is not financial advice. Trading stocks involves substantial risk of loss. Past performance is not indicative of future results. You could lose some or all of your invested capital. Do not trade with money you cannot afford to lose. By using this software, you acknowledge that you are solely responsible for your own trading decisions and any resulting financial outcomes.**

Algorithmic momentum day-trading bot for Charles Schwab. Built for small accounts with aggressive risk management.

## Strategy

**Momentum Surge** -- Ross Cameron-style breakout trading on 5-minute bars.

- **Scanner**: TradingView screener for top gainers, enriched with relative volume and float data
- **Entry**: Price near recent high, above VWAP, RSI 55-90, volume surge > 1.5x average, strong bar close
- **Exit**: 2 consecutive closes below VWAP, RSI collapse (< 40), or price below 10-bar low
- **Stop**: ATR x 2.0 below entry, with progressive R-trailing stop
- **Target**: 3:1 risk/reward
- **Schedule**: Scanning 6:00 AM - 4:00 PM ET, safety net close at 3:55 PM ET
- **Position sizing**: Up to 90% of buying power, max 2% account risk per trade
- **Max trades/day**: 10 (max 2 per symbol)

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
- `SCHWAB_CLIENT_ID` -- OAuth client ID from Schwab developer portal
- `SCHWAB_CLIENT_SECRET` -- OAuth client secret
- `SCHWAB_ACCOUNT_NUMBER` -- your Schwab account number
- `TRADING_MODE` -- `paper` for paper trading, `live` for real money

**Optional but recommended:**
- `FMP_API_KEY` -- enables float filtering (free tier: 250 requests/day)

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
│   │   ├── api.py               # FastAPI dashboard server
│   │   ├── config.py            # Bot-specific config (strategy params)
│   │   ├── executor.py          # Signal -> order execution
│   │   ├── processor.py         # Signal filtering and validation
│   │   ├── scheduler.py         # APScheduler job management
│   │   ├── screener.py          # Stock screener logic
│   │   ├── tradingview_screener.py  # TradingView API integration
│   │   ├── stream_handler.py    # WebSocket bar aggregation
│   │   ├── monitor.py           # Position monitoring and P&L
│   │   ├── float_provider.py    # Float data from FMP API
│   │   ├── press_release_scanner.py # Pre-market catalyst detection
│   │   ├── signals/             # Signal generation strategies
│   │   │   ├── base.py          # Abstract signal base class
│   │   │   ├── momentum_surge.py    # Primary strategy
│   │   │   ├── momentum_pullback.py # Pullback after surge
│   │   │   ├── breakout.py      # Price breakout
│   │   │   ├── macd.py          # MACD crossover
│   │   │   ├── macd_systems.py  # Complex MACD systems
│   │   │   └── mean_reversion.py    # Mean reversion
│   │   └── state/               # State persistence
│   │       ├── persistence.py   # Bot state file I/O
│   │       └── trade_ledger.py  # Trade history tracking
│   ├── core/                    # Broker integration
│   │   ├── tastytrade_client.py # REST API wrapper
│   │   ├── tastytrade_ws.py     # DXLink WebSocket streaming
│   │   ├── order_executor.py    # Order submission
│   │   ├── position_manager.py  # Position tracking
│   │   └── regime_detector.py   # HMM market regime detection
│   ├── risk/                    # Risk management
│   │   ├── portfolio_limits.py  # Portfolio-level limits
│   │   ├── position_sizer.py    # Position sizing
│   │   └── stop_manager.py      # Stop-loss and trailing stops
│   └── data/
│       └── indicators.py        # Technical indicators
├── scripts/
│   ├── run_bot.py               # Main entry point
│   ├── healthcheck.sh           # Remote health monitoring
│   ├── backtest_surge.py        # Backtest momentum surge
│   └── ...                      # Other backtest scripts
├── deploy/
│   ├── podman-compose.yml       # Container orchestration
│   ├── deploy-remote.sh         # Remote deployment script
│   ├── sgt-schwab.service       # systemd service file
│   ├── supervisor.conf          # Supervisor config
│   └── com.jacobmadsen.sgt-schwab.plist  # macOS launchd config
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
| `SCHWAB_CLIENT_ID` | Yes | OAuth client ID from Schwab developer portal |
| `SCHWAB_CLIENT_SECRET` | Yes | OAuth client secret |
| `SCHWAB_ACCOUNT_NUMBER` | Yes | Your Schwab account number |

### Trading

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `ENABLE_EXTENDED_HOURS` | `true` | Allow extended hours trading |

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

For background execution on macOS, edit `deploy/com.jacobmadsen.sgt-schwab.plist` with your paths and load it:

```bash
cp deploy/com.jacobmadsen.sgt-schwab.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jacobmadsen.sgt-schwab.plist
```

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

**Optional: systemd auto-start** -- copy and enable the service file:

```bash
ssh jacisjake@ut.gitsum.rest
sudo cp /opt/sgt-schwab/deploy/sgt-schwab.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sgt-schwab
```

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

Several backtest scripts are included in `scripts/`:

```bash
# Backtest the momentum surge strategy
python scripts/backtest_surge.py

# Backtest with today's data
python scripts/backtest_today.py

# Diagnose signal generation
python scripts/backtest_diagnose.py
```

## Architecture

The bot runs as a single async process with these components:

1. **Scheduler** (APScheduler) -- triggers scanner, monitor, and sync jobs on intervals
2. **Scanner** (TradingView + FMP) -- finds candidate stocks matching momentum criteria
3. **WebSocket** (DXLink) -- streams real-time 5-min bars and quotes for watchlist symbols
4. **Signal Generator** -- evaluates bars against strategy rules, emits buy/sell signals
5. **Executor** -- converts signals to broker orders with position sizing and risk checks
6. **Position Monitor** -- tracks open positions, manages trailing stops, triggers exits
7. **Risk Manager** -- enforces per-trade, portfolio, and daily loss limits
8. **Dashboard** (FastAPI) -- web UI for monitoring, manual controls, trade history, and OAuth flow

## License

MIT
