import os

import pytest

from config.settings import TradingMode
from src.bot.config import BotConfig


def test_trading_mode_dry_run_replaces_paper():
    assert TradingMode.DRY_RUN.value == "dry_run"
    assert TradingMode.LIVE.value == "live"
    assert not hasattr(TradingMode, "PAPER")


def test_default_trading_mode_is_dry_run(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    config = BotConfig()
    assert config.trading_mode == TradingMode.DRY_RUN
    assert config.is_dry_run
    assert not config.is_live


def test_schwab_fields_present():
    config = BotConfig()
    assert hasattr(config, "schwab_app_key")
    assert hasattr(config, "schwab_app_secret")
    assert hasattr(config, "schwab_oauth_redirect_uri")
    assert hasattr(config, "schwab_token_path")
    assert hasattr(config, "schwab_account_hash")


def test_surge_fields_removed():
    config = BotConfig()
    for removed in (
        "min_signal_strength",
        "enable_regime_gate",
        "regime_symbol",
        "regime_hmm_states",
        "regime_history_days",
        "pullback_min_candles",
        "enable_press_release_scanner",
        "press_release_lookback_hours",
        "scanner_enable_news_check",
        "fmp_api_key",
    ):
        assert not hasattr(config, removed), f"{removed} should be deleted"


def test_enable_orb_live_defaults_false(monkeypatch):
    monkeypatch.delenv("ENABLE_ORB_LIVE", raising=False)
    config = BotConfig()
    assert config.enable_orb_live is False


@pytest.mark.parametrize("value", ["true", "1", "True", "TRUE"])
def test_enable_orb_live_env_true(monkeypatch, value):
    monkeypatch.setenv("ENABLE_ORB_LIVE", value)
    config = BotConfig()
    assert config.enable_orb_live is True


@pytest.mark.parametrize("value", ["false", "0", "False"])
def test_enable_orb_live_env_false(monkeypatch, value):
    monkeypatch.setenv("ENABLE_ORB_LIVE", value)
    config = BotConfig()
    assert config.enable_orb_live is False
