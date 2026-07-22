"""
Bot-specific configuration for momentum day trading.

Extends base Settings with scanner, strategy, and scheduler parameters.
Targeting $1-$10 low-float stocks (prefer $2+) on 5-min bars with ORB entries.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from config.settings import Settings


class BotConfig(Settings):
    """
    Momentum day trading bot configuration.

    Strategy: ORB (Opening Range Breakout) on low-float momentum stocks.
    Timeframe: 5-minute bars, 6:00 AM - 3:55 PM ET.
    Goal: One high-quality trade per day, 10% account growth.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Schwab OAuth credentials ─────────────────────────────────────────

    schwab_app_key: str = Field(default="", env="SCHWAB_APP_KEY")
    schwab_app_secret: str = Field(default="", env="SCHWAB_APP_SECRET")
    schwab_oauth_redirect_uri: str = Field(
        default="https://ut.gitsum.rest/schwab/oauth/callback",
        env="SCHWAB_OAUTH_REDIRECT_URI",
    )
    schwab_token_path: str = Field(default="state/schwab_token.json", env="SCHWAB_TOKEN_PATH")
    schwab_account_hash: Optional[str] = Field(default=None, env="SCHWAB_ACCOUNT_HASH")

    # ── Scheduler Settings ──────────────────────────────────────────────

    stock_check_interval_minutes: int = Field(
        default=1,
        ge=1,
        le=60,
        description="How often to run momentum scan during trading window",
    )
    position_monitor_interval_seconds: int = Field(
        default=30,
        ge=10,
        le=120,
        description="How often to check position exits (seconds)",
    )
    broker_sync_interval_minutes: int = Field(
        default=1,
        ge=1,
        le=30,
        description="How often to sync with broker positions",
    )
    scanner_refresh_interval_minutes: int = Field(
        default=2,
        ge=1,
        le=30,
        description="How often to refresh scanner results during trading window",
    )

    # ── Schedule (Eastern Time) ──────────────────────────────────────────

    premarket_scan_start: str = Field(
        default="06:00",
        description="When to start pre-market scanning (ET, HH:MM)",
    )

    # ── Momentum Scanner Settings ───────────────────────────────────────
    # Ross Cameron's 5 pillars: price, float, relative volume, change%, catalyst

    scanner_min_price: float = Field(
        default=2.50,
        ge=0.50,
        le=20.0,
        description="Minimum stock price for scanner ($2.50 floor)",
    )
    scanner_preferred_min_price: float = Field(
        default=2.0,
        ge=1.0,
        le=20.0,
        description="Preferred minimum price — stocks above this get priority weighting",
    )
    scanner_max_price: float = Field(
        default=10.0,
        ge=2.0,
        le=50.0,
        description="Maximum stock price for scanner ($10 ceiling for low-float momentum)",
    )
    scanner_min_change_pct: float = Field(
        default=10.0,
        ge=5.0,
        le=50.0,
        description="Minimum % gain today to qualify (already moving)",
    )
    scanner_min_dollar_volume: float = Field(
        default=500_000,
        ge=50_000,
        le=50_000_000,
        description="Minimum dollar volume traded today (price * volume) for liquidity",
    )
    scanner_min_float_millions: float = Field(
        default=0.5,
        ge=0.1,
        le=50.0,
        description="Minimum float in millions (enough liquidity to avoid slippage)",
    )
    scanner_enable_float_filter: bool = Field(
        default=True,
        description="Enable float filtering",
    )
    scanner_top_n: int = Field(
        default=20,
        ge=5,
        le=50,
        description="Number of gainers to fetch from screener",
    )

    # ── TradingView Screener (Primary Scanner) ──────────────────────────

    use_tradingview_screener: bool = Field(
        default=True,
        description="Use TradingView as primary screener",
    )

    # ── MACD Strategy Parameters ────────────────────────────────────────

    macd_fast_period: int = Field(
        default=8,
        ge=3,
        le=20,
        description="MACD fast EMA period",
    )
    macd_slow_period: int = Field(
        default=21,
        ge=10,
        le=50,
        description="MACD slow EMA period",
    )
    macd_signal_period: int = Field(
        default=5,
        ge=1,
        le=20,
        description="MACD signal line EMA period",
    )
    stock_timeframe: str = Field(
        default="5Min",
        description="Entry timeframe for signals (5-min bars for day trading)",
    )
    stock_atr_stop_multiplier: float = Field(
        default=1.5,
        ge=0.5,
        le=4.0,
        description="ATR multiplier for stop-loss",
    )
    atr_period: int = Field(
        default=14,
        ge=5,
        le=30,
        description="ATR calculation period",
    )
    chandelier_multiplier: float = Field(
        default=3.0,
        ge=1.0,
        le=6.0,
        description="Chandelier exit ATR multiplier for progressive trailing stop",
    )

    # ── Pullback Pattern Parameters (kept for trailing-stop logic) ──────

    pullback_max_candles: int = Field(
        default=15,
        ge=3,
        le=25,
        description="Maximum candles in pullback (volatile stocks consolidate 10-15 candles)",
    )
    pullback_max_retracement: float = Field(
        default=0.65,
        ge=0.20,
        le=0.80,
        description="Maximum pullback retracement of surge",
    )
    risk_reward_target: float = Field(
        default=2.0,
        ge=1.0,
        le=20.0,
        description="Take-profit target as multiple of risk (2R = 2x stop distance)",
    )

    # ── Signal Filtering ────────────────────────────────────────────────

    min_risk_reward: float = Field(
        default=1.0,
        ge=0.5,
        le=5.0,
        description="Minimum risk/reward ratio to accept trade",
    )
    allow_short_selling: bool = Field(
        default=False,
        description="Allow short selling (not used in momentum strategy)",
    )

    # ── Capital safety (ORB live money path) ────────────────────────────
    # Default False: even if TRADING_MODE=live, TradeExecutor will not
    # submit real ORB broker orders. dry_run already blocks the broker
    # in OrderExecutor. Set ENABLE_ORB_LIVE=true only when intentionally
    # re-enabling ORB live money.

    enable_orb_live: bool = Field(
        default=False,
        env="ENABLE_ORB_LIVE",
        description="Allow real ORB broker order submits when TRADING_MODE=live",
    )

    # ── Day Trading Risk Management ─────────────────────────────────────

    max_daily_trades: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum trades per day",
    )
    daily_profit_target_pct: float = Field(
        default=0.10,
        ge=0.02,
        le=0.30,
        description="Daily profit target (10% of account)",
    )
    daily_loss_limit_pct: float = Field(
        default=0.10,
        ge=0.02,
        le=0.20,
        description="Maximum daily loss before halt (10% of account)",
    )
    max_position_pct_of_buying_power: float = Field(
        default=0.90,
        ge=0.25,
        le=1.0,
        description="Max % of buying power to use per trade (cash account style)",
    )

    # ── Watchlist (scanner-driven, no static list) ──────────────────────

    stock_watchlist: str = Field(
        default="",
        description="Static stock watchlist (empty = fully scanner-driven)",
    )

    # ── WebSocket Settings ────────────────────────────────────────────

    ws_reconnect_max_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Max reconnect backoff in seconds for WebSocket",
    )
    ws_heartbeat_seconds: int = Field(
        default=30,
        ge=10,
        le=60,
        description="WebSocket ping interval in seconds",
    )

    # ── State Files ─────────────────────────────────────────────────────

    state_dir: str = Field(
        default="state",
        description="Directory for state files",
    )

    # ── Alerting (email over SMTP) ──────────────────────────────────────
    # Schwab refresh tokens die every 7 days and cannot be renewed by API, so
    # the only defence is being told before/when it happens. Unset = alerts are
    # skipped (logged), never fatal.

    alert_email_to: str = Field(
        default="",
        description="Recipient address for token-expiry and failure alerts",
    )
    alert_warn_within_days: float = Field(
        default=2.0,
        ge=0.5,
        le=6.0,
        description="Warn this many days before the Schwab refresh token expires",
    )
    smtp_host: str = Field(default="", description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP port (587 = STARTTLS)")
    smtp_user: str = Field(default="", description="SMTP username")
    smtp_password: str = Field(default="", description="SMTP password / app password")
    smtp_from: str = Field(
        default="",
        description="From address (defaults to smtp_user when empty)",
    )

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def stock_symbols(self) -> list[str]:
        """Parse stock watchlist into list (may be empty if scanner-driven)."""
        return [s.strip().upper() for s in self.stock_watchlist.split(",") if s.strip()]

    @property
    def state_path(self) -> Path:
        """Get state directory path."""
        return Path(self.state_dir)

    @property
    def bot_state_file(self) -> Path:
        """Get bot state file path."""
        return self.state_path / "bot_state.json"


def get_bot_config() -> BotConfig:
    """Get bot configuration instance."""
    return BotConfig()
