# Schwab Migration & ORB Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tastytrade with the Charles Schwab API and replace momentum-surge with Opening Range Breakout (ORB), in a single big-bang rewrite on the `cleaning` branch, with a dry-run trading mode as the cutover safety net.

**Architecture:** New `SchwabClient` wraps `schwab-py` for REST; new `SchwabStreamClient` wraps `schwab-py`'s `StreamClient` with a 1-min→5-min bar aggregator and the same callback shape the existing stream handler expects. The order executor keeps its public methods but swaps internals to Schwab order builders, with a dry-run intercept that fabricates fills. The strategy layer is collapsed to a single `OpeningRangeBreakout` module that locks the 9:30–9:45 ET range via REST `pricehistory` and emits a long signal on the first 5-min close above the range high (with a volume filter). Surge/pullback/regime/press-release modules and their config keys are deleted entirely. OAuth is path-scoped to `/schwab/oauth/*` and the dashboard is updated to show ORB state.

**Tech Stack:** Python 3.11+, `schwab-py`, `httpx` + `respx` for HTTP mocking, FastAPI/uvicorn for dashboard, pytest (asyncio_mode=auto), Podman + Caddy for deployment.

**Spec:** `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`

---

## File Structure

### Files created
- `src/core/schwab_client.py` — REST wrapper around `schwab-py`
- `src/core/schwab_stream.py` — async streamer wrapper with 1-min→5-min bar aggregator
- `src/core/market_calendar.py` — `NYSE_HOLIDAYS` and session helpers (extracted from `tastytrade_client.py`)
- `src/bot/signals/orb.py` — Opening Range Breakout strategy
- `src/bot/web.py` — FastAPI dashboard with Schwab OAuth + ORB endpoints (built from scratch — prior surge dashboard was discarded with the rest of the surge work)
- `tests/unit/test_schwab_client.py`
- `tests/unit/test_schwab_stream.py`
- `tests/unit/test_bar_aggregator.py`
- `tests/unit/test_orb_strategy.py`
- `tests/unit/test_dry_run_executor.py`
- `tests/unit/test_market_calendar.py`
- `tests/unit/test_oauth_routes.py`
- `tests/unit/test_dashboard_endpoints.py`
- `scripts/smoke_schwab.py`

### Files modified
- `requirements.txt` — add `schwab-py`, `respx`; drop `tastytrade`, `hmmlearn`, `scikit-learn`
- `.env.example` — replace TT_* with SCHWAB_*; drop FMP_API_KEY; switch trading mode
- `config/settings.py` — drop `TradingMode.PAPER`, add `TradingMode.DRY_RUN`
- `src/bot/config.py` — drop surge fields; add Schwab fields
- `src/bot/signals/base.py` — slim `Signal` dataclass
- `src/bot/processor.py` — drop regime gate, catalyst path, min-strength
- `src/bot/main.py` — swap imports + init for SchwabClient/SchwabStreamClient + ORB
- `src/bot/monitor.py` — swap import to `SchwabClient`
- `src/bot/stream_handler.py` — swap imports
- `src/bot/scheduler.py` — import `NYSE_HOLIDAYS` from `market_calendar`; add `or_lock` job at 9:45:30 ET
- `src/core/order_executor.py` — rewrite internals against SchwabClient + dry-run intercept
- `scripts/run_bot.py` — drop `--check-signals` (surge-specific); update `show_status` import; remove `run_with_api` references to old web module if any
- `deploy/deploy-remote.sh` — paths + container names
- `tests/conftest.py` — fixtures for `mock_schwab_py_client`

### Files deleted
- `src/core/tastytrade_client.py`
- `src/core/tastytrade_ws.py`
- `src/core/regime_detector.py`
- `src/bot/press_release_scanner.py`
- `src/bot/signals/momentum_pullback.py`
- `src/bot/signals/momentum_surge.py`
- `scripts/backtest_press_releases.py`
- `scripts/backtest_morning.py` (surge-specific)

---

## Phase 1 — Demolition

Removes the surge surface so subsequent phases land in a clean slate.

### Task 1: Delete dead strategy and detector modules

**Files:**
- Delete: `src/core/regime_detector.py`
- Delete: `src/bot/press_release_scanner.py`
- Delete: `src/bot/signals/momentum_pullback.py`
- Delete: `src/bot/signals/momentum_surge.py`
- Delete: `scripts/backtest_press_releases.py`
- Delete: `scripts/backtest_morning.py`

- [ ] **Step 1: Confirm no surviving consumers outside the kill list**

```bash
grep -rnE "(regime_detector|RegimeDetector|press_release_scanner|PressReleaseScanner|momentum_pullback|MomentumPullback|momentum_surge|MomentumSurgeStrategy)" \
    --include="*.py" src/ scripts/ tests/ \
    | grep -vE "^(src/core/regime_detector\.py|src/bot/press_release_scanner\.py|src/bot/signals/momentum_(pullback|surge)\.py|scripts/backtest_(press_releases|morning)\.py):"
```
Expected output: lists `src/bot/main.py`, `src/bot/stream_handler.py`, `src/bot/processor.py`, possibly `tests/`. Note these — they get cleaned up in later tasks.

- [ ] **Step 2: Delete the files**

```bash
git rm src/core/regime_detector.py \
       src/bot/press_release_scanner.py \
       src/bot/signals/momentum_pullback.py \
       src/bot/signals/momentum_surge.py \
       scripts/backtest_press_releases.py \
       scripts/backtest_morning.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "Delete surge/regime/press-release modules

Migration to Schwab + ORB removes these strategy and enrichment
modules entirely. Consumers in main.py / stream_handler.py / processor.py
will be updated in subsequent commits."
```

---

### Task 2: Slim the Signal dataclass

**Files:**
- Modify: `src/bot/signals/base.py`
- Test: `tests/unit/test_signal_base.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_signal_base.py`:
```python
from datetime import datetime

import pytest

from src.bot.signals.base import Signal, SignalDirection


def test_signal_minimal_construction():
    s = Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.5,
        target_price=11.0,
        strategy="orb",
    )
    assert s.symbol == "AAPL"
    assert s.risk_amount == pytest.approx(0.5)
    assert s.risk_reward_ratio == pytest.approx(2.0)
    assert isinstance(s.timestamp, datetime)
    assert s.metadata == {}


def test_signal_long_validation():
    with pytest.raises(ValueError, match="Long stop must be below entry"):
        Signal(
            symbol="AAPL",
            direction=SignalDirection.LONG,
            entry_price=10.0,
            stop_price=10.5,
            target_price=11.0,
            strategy="orb",
        )


def test_signal_drops_strength_field():
    # Surge-era 'strength' attribute is removed
    s = Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.0,
        target_price=12.0,
        strategy="orb",
    )
    assert not hasattr(s, "strength")
    assert not hasattr(s, "strength_category")
    assert not hasattr(s, "has_catalyst")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_signal_base.py -v
```
Expected: failures on `test_signal_minimal_construction` (TypeError: missing strength), `test_signal_drops_strength_field` (hasattr returns True).

- [ ] **Step 3: Replace `src/bot/signals/base.py`**

```python
"""
Base classes for signal generation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import pandas as pd


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""

    symbol: str
    direction: SignalDirection
    entry_price: float
    stop_price: float
    target_price: float
    strategy: str
    timestamp: datetime = field(default_factory=datetime.now)
    timeframe: str = "5Min"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.direction == SignalDirection.LONG:
            if self.stop_price >= self.entry_price:
                raise ValueError("Long stop must be below entry")
            if self.target_price <= self.entry_price:
                raise ValueError("Long target must be above entry")
        else:
            if self.stop_price <= self.entry_price:
                raise ValueError("Short stop must be above entry")
            if self.target_price >= self.entry_price:
                raise ValueError("Short target must be below entry")

    @property
    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_amount(self) -> float:
        return abs(self.target_price - self.entry_price)

    @property
    def risk_reward_ratio(self) -> float:
        return self.reward_amount / self.risk_amount

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "risk_amount": self.risk_amount,
            "risk_reward": self.risk_reward_ratio,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class SignalGenerator(ABC):
    """Base class strategies inherit from."""

    @abstractmethod
    def generate(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_price: float,
    ) -> Optional[Signal]:
        ...
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_signal_base.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_signal_base.py src/bot/signals/base.py
git commit -m "Slim Signal dataclass for ORB

Drop surge-era fields (strength, strength_category, has_catalyst,
news_*) and the SignalStrength enum. Target_price becomes required
since ORB always emits one. SignalGenerator's generate() loses the
has_catalyst kwarg."
```

---

### Task 3: Update SignalProcessor — drop regime/catalyst/min-strength

**Files:**
- Modify: `src/bot/processor.py`
- Test: `tests/unit/test_processor.py` (existing or new)

- [ ] **Step 1: Inspect current processor and write a covering test**

```bash
grep -nE "min_signal_strength|regime|catalyst|has_catalyst" src/bot/processor.py
```

Create or replace `tests/unit/test_processor.py` with:
```python
from unittest.mock import MagicMock

import pytest

from src.bot.config import BotConfig
from src.bot.processor import SignalProcessor
from src.bot.signals.base import Signal, SignalDirection


def make_signal() -> Signal:
    return Signal(
        symbol="AAPL",
        direction=SignalDirection.LONG,
        entry_price=10.0,
        stop_price=9.0,
        target_price=12.0,
        strategy="orb",
    )


def test_processor_passes_when_limits_ok():
    config = BotConfig()
    portfolio_limits = MagicMock()
    portfolio_limits.check_can_trade.return_value = (True, None, [])
    position_sizer = MagicMock()
    position_sizer.calculate.return_value = MagicMock(
        quantity=10, entry_price=10.0, stop_price=9.0, target_price=12.0
    )

    proc = SignalProcessor(
        config=config,
        position_sizer=position_sizer,
        portfolio_limits=portfolio_limits,
    )
    result = proc.process(
        signal=make_signal(),
        account_equity=270.0,
        buying_power=270.0,
        current_positions=0,
        daytrade_count=0,
    )
    assert result.passed
    assert result.trade_params.quantity == 10


def test_processor_rejects_when_portfolio_limit_blocks():
    config = BotConfig()
    portfolio_limits = MagicMock()
    portfolio_limits.check_can_trade.return_value = (False, "Daily loss limit hit", [])
    position_sizer = MagicMock()

    proc = SignalProcessor(
        config=config,
        position_sizer=position_sizer,
        portfolio_limits=portfolio_limits,
    )
    result = proc.process(
        signal=make_signal(),
        account_equity=270.0,
        buying_power=270.0,
        current_positions=0,
        daytrade_count=0,
    )
    assert not result.passed
    assert "Daily loss limit" in result.rejection_reason


def test_processor_takes_no_regime_argument():
    """Regime gate has been removed."""
    import inspect

    sig = inspect.signature(SignalProcessor.__init__)
    assert "regime_detector" not in sig.parameters
```

- [ ] **Step 2: Run the tests**

```bash
pytest tests/unit/test_processor.py -v
```
Expected: failures (`regime_detector` is still a constructor parameter; possibly extra rejections from missing min-strength gate).

- [ ] **Step 3: Edit `src/bot/processor.py`** — remove these in order:
  - the `regime_detector` constructor parameter and field
  - the `min_signal_strength` check inside `process()`
  - any branch that reads `signal.metadata.get("has_catalyst")` or applies a strength bonus
  - any reference to `signal.strength`

The remaining `process()` body should: build trade params via `position_sizer.calculate(...)`, call `portfolio_limits.check_can_trade(...)`, return a `ProcessResult(passed=..., trade_params=..., rejection_reason=..., warnings=...)`.

- [ ] **Step 4: Run the tests**

```bash
pytest tests/unit/test_processor.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_processor.py src/bot/processor.py
git commit -m "Strip regime/catalyst/min-strength from SignalProcessor

ORB doesn't use any of these. The processor now only checks portfolio
limits and sizes the position."
```

---

### Task 4: Slim BotConfig and switch TradingMode

**Files:**
- Modify: `config/settings.py`
- Modify: `src/bot/config.py`
- Test: `tests/unit/test_config.py` (new or extended)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config.py`:
```python
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
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/unit/test_config.py -v
```
Expected: failures (PAPER still present, surge fields still defined).

- [ ] **Step 3: Edit `config/settings.py`** — replace the `TradingMode` enum:

```python
from enum import Enum


class TradingMode(str, Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"
```

Update the validator/default at the `Settings` class to default to `TradingMode.DRY_RUN` and accept legacy `paper` strings only by raising a clear error:
```python
@field_validator("trading_mode", mode="before")
@classmethod
def validate_trading_mode(cls, v):
    if isinstance(v, TradingMode):
        return v
    if v == "paper":
        raise ValueError(
            "TRADING_MODE='paper' is no longer supported. Use 'dry_run' (simulated fills) or 'live'."
        )
    return TradingMode(v)
```

Update `is_paper` → `is_dry_run`:
```python
@property
def is_dry_run(self) -> bool:
    return self.trading_mode == TradingMode.DRY_RUN

@property
def is_live(self) -> bool:
    return self.trading_mode == TradingMode.LIVE
```

- [ ] **Step 4: Edit `src/bot/config.py`** — delete the surge fields listed in the test and add Schwab fields.

Find and delete each of: `min_signal_strength`, `enable_regime_gate`, `regime_*`, `pullback_*`, `chandelier_multiplier` should *stay* (used by trailing stop), all `press_release_*`, `enable_press_release_scanner`, `scanner_enable_news_check`, `scanner_news_*`, `fmp_api_key`. (Verify each removal compiles by running grep against the codebase before committing.)

Add the Schwab fields. Inside the BotConfig class definition:
```python
    schwab_app_key: str = Field(default="", env="SCHWAB_APP_KEY")
    schwab_app_secret: str = Field(default="", env="SCHWAB_APP_SECRET")
    schwab_oauth_redirect_uri: str = Field(
        default="https://ut.gitsum.rest/schwab/oauth/callback",
        env="SCHWAB_OAUTH_REDIRECT_URI",
    )
    schwab_token_path: str = Field(default="state/schwab_token.json", env="SCHWAB_TOKEN_PATH")
    schwab_account_hash: Optional[str] = Field(default=None, env="SCHWAB_ACCOUNT_HASH")
```

Replace any `tt_*` field with the Schwab equivalent above; delete tastytrade-specific fields entirely.

- [ ] **Step 5: Run the test and clean up follow-on imports**

```bash
pytest tests/unit/test_config.py -v
```
Expected: 4 passed.

```bash
grep -rnE "(is_paper|TradingMode\.PAPER)" --include="*.py" src/ scripts/ tests/
```
Replace each hit (`is_paper` → `is_dry_run`, `TradingMode.PAPER` → `TradingMode.DRY_RUN`).

- [ ] **Step 6: Commit**

```bash
git add config/settings.py src/bot/config.py tests/unit/test_config.py
git commit -m "Switch TradingMode to {DRY_RUN, LIVE}; slim BotConfig

Add Schwab OAuth fields, drop surge/regime/press-release/FMP keys.
'paper' mode is rejected with a clear migration error."
```

---

### Task 5: Replace .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Replace `.env.example` entirely**

```
# ─── Schwab Authentication ────────────────────────────────────────────────────
# 1. Create an app at https://developer.schwab.com
# 2. Set the callback URL exactly to:
#      https://ut.gitsum.rest/schwab/oauth/callback
#    (Schwab matches this byte-for-byte at token exchange.)
# 3. Paste the app key and secret below.
# 4. On first run, visit the bot dashboard and click "Authorize" — the bot
#    will write state/schwab_token.json and refresh it automatically.
SCHWAB_APP_KEY=your_schwab_app_key
SCHWAB_APP_SECRET=your_schwab_app_secret
SCHWAB_OAUTH_REDIRECT_URI=https://ut.gitsum.rest/schwab/oauth/callback
SCHWAB_TOKEN_PATH=state/schwab_token.json

# Optional: pin to a specific account if multiple are linked. If unset,
# the first hash returned by get_account_numbers() is used.
SCHWAB_ACCOUNT_HASH=

# ─── Trading Mode ─────────────────────────────────────────────────────────────
# 'dry_run' = simulated fills, no orders sent (safe default).
# 'live'    = real orders. Flip only after a clean dry-run trading day.
TRADING_MODE=dry_run

# ─── Risk Management ──────────────────────────────────────────────────────────
MAX_POSITION_RISK_PCT=0.01
MAX_PORTFOLIO_RISK_PCT=0.10
MAX_POSITIONS=1
MAX_DRAWDOWN_PCT=0.15

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "Replace .env.example with Schwab + dry-run schema"
```

---

## Phase 2 — Schwab REST client

### Task 6: Add `schwab-py` dependency and write `SchwabClient` skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `src/core/schwab_client.py`
- Create: `tests/unit/test_schwab_client.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update `requirements.txt`**

Drop the `tastytrade` line. Drop `hmmlearn` and `scikit-learn` (regime detector is gone). Add:
```
schwab-py>=1.5.0
respx>=0.20.0
```

Run:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
Expected: clean install.

- [ ] **Step 2: Add a `mock_schwab_client` fixture in `tests/conftest.py`**

Append to `tests/conftest.py`:
```python
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_schwab_py_client():
    """A MagicMock standing in for schwab.client.Client."""
    client = MagicMock()
    client.get_account_numbers.return_value = MagicMock(
        status_code=200,
        json=lambda: [{"accountNumber": "111", "hashValue": "HASH-AAA"}],
    )
    return client
```

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_schwab_client.py`:
```python
from unittest.mock import MagicMock, patch

import pytest

from src.core.schwab_client import SchwabClient


@pytest.fixture
def schwab(mock_schwab_py_client):
    with patch("src.core.schwab_client.easy_client", return_value=mock_schwab_py_client):
        client = SchwabClient(
            app_key="K", app_secret="S",
            callback_url="https://ut.gitsum.rest/schwab/oauth/callback",
            token_path="/tmp/token.json",
        )
        client._client = mock_schwab_py_client
        yield client


def test_authenticated_after_construction(schwab):
    assert schwab.is_authenticated is True


def test_account_hash_resolved_from_first_account(schwab):
    assert schwab.account_hash == "HASH-AAA"


def test_account_hash_pinned_via_constructor(mock_schwab_py_client):
    with patch("src.core.schwab_client.easy_client", return_value=mock_schwab_py_client):
        client = SchwabClient(
            app_key="K", app_secret="S",
            callback_url="https://x/y", token_path="/tmp/t.json",
            pinned_account_hash="HASH-BBB",
        )
        assert client.account_hash == "HASH-BBB"


def test_unauthenticated_when_token_missing(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no token")

    monkeypatch.setattr("src.core.schwab_client.easy_client", boom)
    client = SchwabClient(
        app_key="K", app_secret="S",
        callback_url="https://x/y", token_path="/tmp/missing.json",
    )
    assert client.is_authenticated is False
    assert client.account_hash is None
```

- [ ] **Step 4: Run the tests**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: ImportError on `src.core.schwab_client`.

- [ ] **Step 5: Implement `src/core/schwab_client.py`**

```python
"""
SchwabClient — a thin REST wrapper around schwab-py.

Hides account-hash routing, the schwab.orders DSL, and pricehistory enum
mapping from the rest of the bot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

try:
    from schwab.auth import easy_client
except ImportError:  # pragma: no cover — surfaced at install time
    easy_client = None


class SchwabClient:
    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        callback_url: str,
        token_path: str,
        pinned_account_hash: Optional[str] = None,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._callback_url = callback_url
        self._token_path = token_path
        self._pinned_account_hash = pinned_account_hash
        self._client = None
        self._account_hash: Optional[str] = None
        self._load_or_init()

    def _load_or_init(self) -> None:
        if not (self._app_key and self._app_secret):
            logger.warning("[SCHWAB] No app credentials in env — bot starts unauthenticated.")
            return
        try:
            self._client = easy_client(
                api_key=self._app_key,
                app_secret=self._app_secret,
                callback_url=self._callback_url,
                token_path=self._token_path,
            )
            self._resolve_account_hash()
        except (FileNotFoundError, Exception) as e:
            logger.warning(f"[SCHWAB] Could not load token: {e}. Awaiting OAuth via dashboard.")
            self._client = None

    def _resolve_account_hash(self) -> None:
        if self._pinned_account_hash:
            self._account_hash = self._pinned_account_hash
            return
        resp = self._client.get_account_numbers()
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_account_numbers failed: {resp.status_code}")
        accounts = resp.json()
        if not accounts:
            raise RuntimeError("Schwab returned no linked accounts")
        self._account_hash = accounts[0]["hashValue"]
        logger.info(f"[SCHWAB] Using account hash {self._account_hash}")

    def reload_from_disk(self) -> None:
        """Called by the OAuth callback after a fresh token is written."""
        self._load_or_init()

    @property
    def is_authenticated(self) -> bool:
        return self._client is not None and self._account_hash is not None

    @property
    def account_hash(self) -> Optional[str]:
        return self._account_hash
```

- [ ] **Step 6: Run the tests**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt tests/conftest.py tests/unit/test_schwab_client.py src/core/schwab_client.py
git commit -m "Add SchwabClient skeleton with token loading + account-hash resolution"
```

---

### Task 7: Implement account / positions / buying-power

**Files:**
- Modify: `src/core/schwab_client.py`
- Modify: `tests/unit/test_schwab_client.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_schwab_client.py`**

```python
def test_get_account_returns_normalized_dict(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {
                    "liquidationValue": 270.0,
                    "cashAvailableForTrading": 250.0,
                    "buyingPower": 250.0,
                },
                "isDayTrader": False,
                "roundTrips": 1,
                "type": "CASH",
                "positions": [],
            }
        },
    )

    out = schwab.get_account()
    assert out["equity"] == 270.0
    assert out["buying_power"] == 250.0
    assert out["cash"] == 250.0
    assert out["daytrade_count"] == 1
    assert out["status"] == "active"


def test_get_buying_power_and_equity(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {
                    "liquidationValue": 270.0,
                    "buyingPower": 250.0,
                    "cashAvailableForTrading": 250.0,
                },
                "isDayTrader": False,
                "roundTrips": 0,
                "type": "CASH",
                "positions": [],
            }
        },
    )
    assert schwab.get_buying_power() == 250.0
    assert schwab.get_equity() == 270.0


def test_get_positions(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_account.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "securitiesAccount": {
                "currentBalances": {"liquidationValue": 270.0, "buyingPower": 250.0,
                                     "cashAvailableForTrading": 250.0},
                "isDayTrader": False, "roundTrips": 0, "type": "CASH",
                "positions": [
                    {
                        "instrument": {"symbol": "AAPL"},
                        "longQuantity": 5.0,
                        "shortQuantity": 0.0,
                        "averagePrice": 10.0,
                        "marketValue": 55.0,
                        "currentDayProfitLoss": 5.0,
                        "currentDayProfitLossPercentage": 10.0,
                    }
                ],
            }
        },
    )

    positions = schwab.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "AAPL"
    assert p["qty"] == 5.0
    assert p["avg_entry_price"] == 10.0
    assert p["current_price"] == pytest.approx(11.0)  # market_value / qty
    assert p["unrealized_pl"] == 5.0
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: failures (methods don't exist yet).

- [ ] **Step 3: Add the methods to `src/core/schwab_client.py`**

```python
    def get_account(self) -> dict:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_account(self._account_hash, fields=["positions"])
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_account failed: {resp.status_code}")
        sa = resp.json()["securitiesAccount"]
        bal = sa.get("currentBalances", {})
        return {
            "equity": float(bal.get("liquidationValue", 0)),
            "buying_power": float(bal.get("buyingPower", 0)),
            "cash": float(bal.get("cashAvailableForTrading", 0)),
            "daytrade_count": int(sa.get("roundTrips", 0)),
            "is_pdt": bool(sa.get("isDayTrader", False)),
            "type": sa.get("type", ""),
            "status": "active",
            "_raw_positions": sa.get("positions", []),
        }

    def get_buying_power(self) -> float:
        return self.get_account()["buying_power"]

    def get_equity(self) -> float:
        return self.get_account()["equity"]

    def get_positions(self) -> list[dict]:
        positions = self.get_account()["_raw_positions"]
        out = []
        for p in positions:
            qty = float(p.get("longQuantity", 0)) - float(p.get("shortQuantity", 0))
            if qty == 0:
                continue
            mkt = float(p.get("marketValue", 0))
            current_price = mkt / qty if qty else 0.0
            out.append({
                "symbol": p["instrument"]["symbol"],
                "qty": qty,
                "avg_entry_price": float(p.get("averagePrice", 0)),
                "current_price": current_price,
                "market_value": mkt,
                "unrealized_pl": float(p.get("currentDayProfitLoss", 0)),
                "unrealized_plpc": float(p.get("currentDayProfitLossPercentage", 0)) / 100,
            })
        return out

    def get_position(self, symbol: str) -> Optional[dict]:
        for p in self.get_positions():
            if p["symbol"] == symbol:
                return p
        return None

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_client.py tests/unit/test_schwab_client.py
git commit -m "SchwabClient: account / positions / buying-power"
```

---

### Task 8: Implement bars / latest_price / quotes

**Files:**
- Modify: `src/core/schwab_client.py`
- Modify: `tests/unit/test_schwab_client.py`

- [ ] **Step 1: Append failing tests**

```python
def test_get_bars_5min(schwab, mock_schwab_py_client):
    candles = [
        {"datetime": 1715170200000, "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4, "volume": 1000},
        {"datetime": 1715170500000, "open": 10.4, "high": 10.7, "low": 10.3, "close": 10.6, "volume": 1500},
    ]
    mock_schwab_py_client.get_price_history_every_five_minutes.return_value = MagicMock(
        status_code=200,
        json=lambda: {"candles": candles, "symbol": "AAPL", "empty": False},
    )

    bars = schwab.get_bars("AAPL", timeframe="5Min", limit=2)
    assert len(bars) == 2
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert bars["close"].iloc[-1] == 10.6


def test_get_latest_price(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_quote.return_value = MagicMock(
        status_code=200,
        json=lambda: {"AAPL": {"quote": {"lastPrice": 10.55}}},
    )
    assert schwab.get_latest_price("AAPL") == 10.55


def test_get_latest_quotes_multi_symbol(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_quotes.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "AAPL": {"quote": {"lastPrice": 10.55, "bidPrice": 10.5, "askPrice": 10.6,
                               "netChange": 0.5, "netPercentChangeInDouble": 5.0}},
            "MSFT": {"quote": {"lastPrice": 20.0, "bidPrice": 19.9, "askPrice": 20.1,
                               "netChange": 1.0, "netPercentChangeInDouble": 5.0}},
        },
    )
    quotes = schwab.get_latest_quotes_with_change(["AAPL", "MSFT"])
    assert quotes["AAPL"]["price"] == 10.55
    assert quotes["AAPL"]["change_pct"] == 5.0
    assert quotes["MSFT"]["bid"] == 19.9
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: failures (methods missing).

- [ ] **Step 3: Add the methods**

```python
    import pandas as pd  # at top of file

    _TIMEFRAME_TO_METHOD = {
        "1Min": "get_price_history_every_minute",
        "5Min": "get_price_history_every_five_minutes",
        "15Min": "get_price_history_every_fifteen_minutes",
        "30Min": "get_price_history_every_thirty_minutes",
        "1Hour": "get_price_history_every_thirty_minutes",  # Schwab has no 60-min; aggregate later if needed
        "1Day": "get_price_history_every_day",
    }

    def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 100) -> "pd.DataFrame":
        import pandas as pd
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        method_name = self._TIMEFRAME_TO_METHOD.get(timeframe)
        if not method_name:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        method = getattr(self._client, method_name)
        resp = method(symbol)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"pricehistory failed: {resp.status_code}")
        candles = resp.json().get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        return df.tail(limit)

    def get_latest_price(self, symbol: str) -> float:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_quote(symbol)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_quote failed: {resp.status_code}")
        return float(resp.json()[symbol]["quote"]["lastPrice"])

    def get_latest_quotes_with_change(self, symbols: list[str]) -> dict:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_quotes(symbols)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_quotes failed: {resp.status_code}")
        out = {}
        for sym, payload in resp.json().items():
            q = payload.get("quote", {})
            out[sym] = {
                "price": float(q.get("lastPrice", 0)),
                "bid": float(q.get("bidPrice", 0)),
                "ask": float(q.get("askPrice", 0)),
                "change": float(q.get("netChange", 0)),
                "change_pct": float(q.get("netPercentChangeInDouble", 0)),
            }
        return out
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_client.py tests/unit/test_schwab_client.py
git commit -m "SchwabClient: bars / latest_price / quotes"
```

---

### Task 9: Implement order submission and cancellation

**Files:**
- Modify: `src/core/schwab_client.py`
- Modify: `tests/unit/test_schwab_client.py`

- [ ] **Step 1: Append failing tests**

```python
def test_submit_market_order_calls_place_order_with_account_hash(schwab, mock_schwab_py_client):
    mock_schwab_py_client.place_order.return_value = MagicMock(
        status_code=201,
        headers={"Location": "https://api.schwabapi.com/.../orders/9876"},
    )

    order_id = schwab.submit_market_order("AAPL", qty=5, side="buy")
    assert order_id == "9876"

    args, kwargs = mock_schwab_py_client.place_order.call_args
    assert args[0] == "HASH-AAA"  # account hash positional


def test_submit_stop_limit_order(schwab, mock_schwab_py_client):
    mock_schwab_py_client.place_order.return_value = MagicMock(
        status_code=201,
        headers={"Location": "https://api.schwabapi.com/.../orders/4321"},
    )

    order_id = schwab.submit_stop_limit_order(
        "AAPL", qty=5, side="sell", stop_price=9.0, limit_price=8.95
    )
    assert order_id == "4321"


def test_cancel_order(schwab, mock_schwab_py_client):
    mock_schwab_py_client.cancel_order.return_value = MagicMock(status_code=200)
    assert schwab.cancel_order("9876") is True
    mock_schwab_py_client.cancel_order.assert_called_once_with("9876", "HASH-AAA")
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: failures.

- [ ] **Step 3: Add the order methods**

```python
    from schwab.orders.equities import (
        equity_buy_market,
        equity_sell_market,
        equity_buy_limit,
        equity_sell_limit,
    )
    from schwab.orders.common import (
        Duration, Session, OrderType, ComplexOrderStrategyType, StopType,
    )
    # ...

    @staticmethod
    def _extract_order_id_from_location(headers: dict) -> str:
        location = headers.get("Location") or headers.get("location") or ""
        return location.rsplit("/", 1)[-1]

    def submit_market_order(self, symbol: str, qty: float, side: str) -> str:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        builder = (
            equity_buy_market(symbol, int(qty))
            if side.lower() == "buy"
            else equity_sell_market(symbol, int(qty))
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def submit_limit_order(
        self, symbol: str, qty: float, side: str, limit_price: float
    ) -> str:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        builder = (
            equity_buy_limit(symbol, int(qty), limit_price)
            if side.lower() == "buy"
            else equity_sell_limit(symbol, int(qty), limit_price)
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def submit_stop_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
        limit_price: float,
    ) -> str:
        """Stop-limit order to protect a position."""
        from schwab.orders.generic import OrderBuilder
        from schwab.orders.common import (
            Duration, Session, OrderType, EquityInstruction,
        )
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")

        instr = (
            EquityInstruction.BUY if side.lower() == "buy"
            else EquityInstruction.SELL
        )
        builder = (
            OrderBuilder()
            .set_order_type(OrderType.STOP_LIMIT)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_stop_price(stop_price)
            .set_price(limit_price)
            .add_equity_leg(
                instruction=instr,
                symbol=symbol,
                quantity=int(qty),
            )
        )
        resp = self._client.place_order(self._account_hash, builder)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"place_order failed: {resp.status_code}")
        return self._extract_order_id_from_location(resp.headers)

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.cancel_order(order_id, self._account_hash)
        return resp.status_code in (200, 201)

    def cancel_all_orders(self) -> int:
        cancelled = 0
        for order in self.get_orders(status="open"):
            if self.cancel_order(order["id"]):
                cancelled += 1
        return cancelled
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: passed (3 new tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_client.py tests/unit/test_schwab_client.py
git commit -m "SchwabClient: market/limit/stop-limit submission + cancel"
```

---

### Task 10: Implement get_orders + get_order_status

**Files:**
- Modify: `src/core/schwab_client.py`
- Modify: `tests/unit/test_schwab_client.py`

- [ ] **Step 1: Append failing tests**

```python
def test_get_orders_normalizes(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_orders_for_account.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {
                "orderId": 1234,
                "status": "FILLED",
                "filledQuantity": 5,
                "orderLegCollection": [{"instrument": {"symbol": "AAPL"}, "quantity": 5}],
                "orderType": "MARKET",
                "price": None,
                "stopPrice": None,
                "enteredTime": "2026-05-08T13:30:00+0000",
            },
            {
                "orderId": 1235,
                "status": "WORKING",
                "filledQuantity": 0,
                "orderLegCollection": [{"instrument": {"symbol": "AAPL"}, "quantity": 5}],
                "orderType": "STOP_LIMIT",
                "price": 9.9,
                "stopPrice": 9.95,
                "enteredTime": "2026-05-08T13:31:00+0000",
            },
        ],
    )

    orders = schwab.get_orders(status="open")
    assert len(orders) == 1
    assert orders[0]["id"] == "1235"
    assert orders[0]["type"] == "stop_limit"
    assert orders[0]["stop_price"] == 9.95


def test_get_orders_status_all(schwab, mock_schwab_py_client):
    mock_schwab_py_client.get_orders_for_account.return_value = MagicMock(
        status_code=200,
        json=lambda: [{"orderId": 1, "status": "FILLED", "filledQuantity": 5,
                       "orderLegCollection": [{"instrument": {"symbol": "X"}, "quantity": 5}],
                       "orderType": "MARKET", "price": None, "stopPrice": None,
                       "enteredTime": "2026-05-08T13:30:00+0000"}],
    )
    assert len(schwab.get_orders(status="all")) == 1
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: failures.

- [ ] **Step 3: Add the methods**

```python
    _OPEN_STATUSES = {"WORKING", "PENDING_ACTIVATION", "QUEUED", "AWAITING_PARENT_ORDER"}

    def get_orders(self, status: str = "open") -> list[dict]:
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        resp = self._client.get_orders_for_account(self._account_hash)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"get_orders_for_account failed: {resp.status_code}")
        out = []
        for o in resp.json():
            if status == "open" and o.get("status") not in self._OPEN_STATUSES:
                continue
            leg = (o.get("orderLegCollection") or [{}])[0]
            out.append({
                "id": str(o.get("orderId")),
                "symbol": leg.get("instrument", {}).get("symbol", ""),
                "qty": float(leg.get("quantity", 0)),
                "filled_qty": float(o.get("filledQuantity", 0)),
                "type": str(o.get("orderType", "")).lower(),
                "status": str(o.get("status", "")).lower(),
                "price": float(o["price"]) if o.get("price") is not None else None,
                "stop_price": float(o["stopPrice"]) if o.get("stopPrice") is not None else None,
                "submitted_at": o.get("enteredTime"),
            })
        return out

    def get_order(self, order_id: str) -> Optional[dict]:
        for o in self.get_orders(status="all"):
            if o["id"] == order_id:
                return o
        return None
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_schwab_client.py -v
```
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_client.py tests/unit/test_schwab_client.py
git commit -m "SchwabClient: get_orders / get_order"
```

---

## Phase 3 — Streaming

### Task 11: BarAggregator (1-min → 5-min)

**Files:**
- Create: `src/core/schwab_stream.py` (skeleton + aggregator)
- Create: `tests/unit/test_bar_aggregator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_bar_aggregator.py`:
```python
from datetime import datetime, timezone

from src.core.schwab_stream import BarAggregator


def _bar(ts_iso: str, o: float, h: float, l: float, c: float, v: int) -> dict:
    return {
        "symbol": "AAPL",
        "timestamp": ts_iso,
        "open": o, "high": h, "low": l, "close": c, "volume": v,
    }


def test_aggregator_emits_one_5min_bar_per_5_inputs():
    emitted: list[dict] = []
    agg = BarAggregator(window_minutes=5, on_emit=lambda b: emitted.append(b))

    # 5 sequential 1-min bars rolling into one 9:30–9:34 5-min bar
    inputs = [
        _bar("2026-05-08T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 100),
        _bar("2026-05-08T13:31:00+00:00", 10.1, 10.3, 10.0, 10.25, 200),
        _bar("2026-05-08T13:32:00+00:00", 10.25, 10.4, 10.2, 10.3, 150),
        _bar("2026-05-08T13:33:00+00:00", 10.3, 10.5, 10.25, 10.45, 250),
        _bar("2026-05-08T13:34:00+00:00", 10.45, 10.6, 10.4, 10.55, 300),
    ]
    for b in inputs:
        agg.feed(b)

    # Aggregation completes when the next-window bar arrives:
    agg.feed(_bar("2026-05-08T13:35:00+00:00", 10.55, 10.6, 10.5, 10.58, 100))

    assert len(emitted) == 1
    out = emitted[0]
    assert out["symbol"] == "AAPL"
    assert out["open"] == 10.0
    assert out["high"] == 10.6
    assert out["low"] == 9.9
    assert out["close"] == 10.55
    assert out["volume"] == 1000


def test_aggregator_does_not_emit_partial_bar():
    emitted: list[dict] = []
    agg = BarAggregator(window_minutes=5, on_emit=lambda b: emitted.append(b))
    agg.feed(_bar("2026-05-08T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 100))
    agg.feed(_bar("2026-05-08T13:31:00+00:00", 10.1, 10.3, 10.0, 10.25, 200))
    assert emitted == []
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_bar_aggregator.py -v
```
Expected: ImportError on `src.core.schwab_stream`.

- [ ] **Step 3: Implement aggregator (skeleton-only `schwab_stream.py`)**

Create `src/core/schwab_stream.py`:
```python
"""SchwabStreamClient + BarAggregator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional


@dataclass
class _Window:
    start_minute: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarAggregator:
    """
    Roll N 1-minute OHLCV bars into a single window-minute bar.

    Each window starts at minute % window_minutes == 0 and closes when a 1-min
    bar arrives whose floored window-start is greater than the current window's
    start. The completed window is emitted via on_emit(bar_dict).
    """

    def __init__(self, *, window_minutes: int, on_emit: Callable[[dict], None]):
        self._window = window_minutes
        self._on_emit = on_emit
        self._open_windows: dict[str, _Window] = {}

    @staticmethod
    def _floor_minute(ts_iso: str, window: int) -> tuple[datetime, int]:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        floored = dt.minute - (dt.minute % window)
        return dt.replace(minute=floored, second=0, microsecond=0), floored

    def feed(self, bar: dict) -> None:
        symbol = bar["symbol"]
        floor_dt, floor_min = self._floor_minute(bar["timestamp"], self._window)
        floor_key = int(floor_dt.timestamp())

        win = self._open_windows.get(symbol)
        if win is None or win.start_minute != floor_key:
            if win is not None:
                self._on_emit({
                    "symbol": symbol,
                    "timestamp": datetime.fromtimestamp(win.start_minute, tz=floor_dt.tzinfo).isoformat(),
                    "open": win.open, "high": win.high, "low": win.low, "close": win.close,
                    "volume": win.volume,
                })
            self._open_windows[symbol] = _Window(
                start_minute=floor_key,
                open=bar["open"], high=bar["high"], low=bar["low"],
                close=bar["close"], volume=bar["volume"],
            )
            return

        win.high = max(win.high, bar["high"])
        win.low = min(win.low, bar["low"])
        win.close = bar["close"]
        win.volume += bar["volume"]
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_bar_aggregator.py -v
```
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_stream.py tests/unit/test_bar_aggregator.py
git commit -m "Add BarAggregator (1-min -> N-min)"
```

---

### Task 12: SchwabStreamClient — connect, login, callbacks

**Files:**
- Modify: `src/core/schwab_stream.py`
- Create: `tests/unit/test_schwab_stream.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.schwab_stream import SchwabStreamClient


@pytest.fixture
def mock_stream_client_class():
    sc = AsyncMock()
    sc.login = AsyncMock(return_value=None)
    sc.handle_message = AsyncMock()
    sc.add_chart_equity_handler = MagicMock()
    sc.add_level_one_equity_handler = MagicMock()
    sc.add_account_activity_handler = MagicMock()
    sc.chart_equity_subs = AsyncMock()
    sc.level_one_equity_subs = AsyncMock()
    sc.account_activity_sub = AsyncMock()
    sc.chart_equity_unsubs = AsyncMock()
    sc.level_one_equity_unsubs = AsyncMock()
    return sc


@pytest.fixture
def stream(mock_stream_client_class):
    schwab_client = MagicMock()
    schwab_client._client = MagicMock()  # underlying schwab-py REST client (StreamClient is built from this)
    with patch("src.core.schwab_stream.StreamClient", return_value=mock_stream_client_class):
        s = SchwabStreamClient(schwab_client=schwab_client)
        s._stream = mock_stream_client_class
        yield s


def test_callbacks_register(stream):
    bar_cb = lambda b: None
    quote_cb = lambda q: None
    trade_cb = lambda t: None
    stream.on_bar(bar_cb)
    stream.on_quote(quote_cb)
    stream.on_trade_update(trade_cb)
    assert bar_cb in stream._bar_callbacks
    assert quote_cb in stream._quote_callbacks
    assert trade_cb in stream._trade_callbacks


@pytest.mark.asyncio
async def test_connect_data_logs_in_and_registers_handlers(stream, mock_stream_client_class):
    ok = await stream.connect_data()
    assert ok is True
    mock_stream_client_class.login.assert_awaited_once()
    mock_stream_client_class.add_chart_equity_handler.assert_called_once()
    mock_stream_client_class.add_level_one_equity_handler.assert_called_once()


@pytest.mark.asyncio
async def test_subscribe_calls_chart_and_quote_subs(stream, mock_stream_client_class):
    await stream.connect_data()
    await stream.subscribe(bars=["AAPL", "MSFT"], quotes=["AAPL"])
    mock_stream_client_class.chart_equity_subs.assert_awaited_once_with(["AAPL", "MSFT"])
    mock_stream_client_class.level_one_equity_subs.assert_awaited_once_with(["AAPL"])
    assert "AAPL" in stream.subscribed_symbols["bars"]
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_schwab_stream.py -v
```
Expected: ImportError on `SchwabStreamClient`.

- [ ] **Step 3: Implement `SchwabStreamClient` in `src/core/schwab_stream.py`**

Add to the file (above the BarAggregator if you like, but the order doesn't matter):
```python
import asyncio
from typing import Callable, List, Optional

from loguru import logger

try:
    from schwab.streaming import StreamClient
except ImportError:  # pragma: no cover
    StreamClient = None


class SchwabStreamClient:
    def __init__(self, *, schwab_client):
        self._schwab = schwab_client
        self._stream: Optional["StreamClient"] = None
        self._bar_callbacks: List[Callable] = []
        self._quote_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []
        self._aggregator = BarAggregator(
            window_minutes=5,
            on_emit=self._dispatch_bar_to_callbacks,
        )
        self._subscribed = {"bars": set(), "quotes": set()}
        self._data_connected = False
        self._trade_connected = False

    # -- Callback registration --------------------------------------------
    def on_bar(self, cb: Callable) -> None:
        self._bar_callbacks.append(cb)

    def on_quote(self, cb: Callable) -> None:
        self._quote_callbacks.append(cb)

    def on_trade_update(self, cb: Callable) -> None:
        self._trade_callbacks.append(cb)

    # -- Connection -------------------------------------------------------
    async def connect_data(self) -> bool:
        try:
            self._stream = StreamClient(self._schwab._client)
            await self._stream.login()
            self._stream.add_chart_equity_handler(self._handle_chart_equity)
            self._stream.add_level_one_equity_handler(self._handle_quote)
            self._data_connected = True
            return True
        except Exception as e:
            logger.error(f"[STREAM] connect_data failed: {e}")
            return False

    async def connect_trades(self) -> bool:
        try:
            if self._stream is None:
                self._stream = StreamClient(self._schwab._client)
                await self._stream.login()
            self._stream.add_account_activity_handler(self._handle_trade_update)
            await self._stream.account_activity_sub()
            self._trade_connected = True
            return True
        except Exception as e:
            logger.error(f"[STREAM] connect_trades failed: {e}")
            return False

    # -- Subscriptions ----------------------------------------------------
    async def subscribe(self, *, bars: List[str] = (), quotes: List[str] = ()) -> None:
        if bars:
            await self._stream.chart_equity_subs(list(bars))
            self._subscribed["bars"].update(bars)
        if quotes:
            await self._stream.level_one_equity_subs(list(quotes))
            self._subscribed["quotes"].update(quotes)

    async def unsubscribe(self, *, bars: List[str] = (), quotes: List[str] = ()) -> None:
        if bars:
            await self._stream.chart_equity_unsubs(list(bars))
            self._subscribed["bars"].difference_update(bars)
        if quotes:
            await self._stream.level_one_equity_unsubs(list(quotes))
            self._subscribed["quotes"].difference_update(quotes)

    async def update_subscriptions(self, *, bars: List[str], quotes: List[str]) -> None:
        cur_bars = self._subscribed["bars"]
        cur_quotes = self._subscribed["quotes"]
        new_bars = set(bars) - cur_bars
        drop_bars = cur_bars - set(bars)
        new_quotes = set(quotes) - cur_quotes
        drop_quotes = cur_quotes - set(quotes)
        if new_bars or new_quotes:
            await self.subscribe(bars=list(new_bars), quotes=list(new_quotes))
        if drop_bars or drop_quotes:
            await self.unsubscribe(bars=list(drop_bars), quotes=list(drop_quotes))

    # -- Status -----------------------------------------------------------
    @property
    def data_connected(self) -> bool:
        return self._data_connected

    @property
    def trade_connected(self) -> bool:
        return self._trade_connected

    @property
    def subscribed_symbols(self) -> dict:
        return {k: set(v) for k, v in self._subscribed.items()}

    def get_status(self) -> dict:
        return {
            "data_connected": self._data_connected,
            "trade_connected": self._trade_connected,
            "subscribed_bars": sorted(self._subscribed["bars"]),
            "subscribed_quotes": sorted(self._subscribed["quotes"]),
        }

    # -- Loops ------------------------------------------------------------
    async def run_data_loop(self) -> None:
        if self._stream is None:
            raise RuntimeError("connect_data() must be called first")
        while True:
            await self._stream.handle_message()

    async def run_trade_loop(self) -> None:
        if self._stream is None:
            raise RuntimeError("connect_trades() must be called first")
        while True:
            await self._stream.handle_message()

    async def disconnect(self) -> None:
        if self._stream is not None:
            try:
                await self._stream.logout()
            except Exception:
                pass
        self._stream = None
        self._data_connected = False
        self._trade_connected = False

    # -- Internal handlers (Schwab → our callback shape) -----------------
    def _handle_chart_equity(self, msg: dict) -> None:
        for content in msg.get("content", []):
            self._aggregator.feed({
                "symbol": content["key"],
                "timestamp": _ms_to_iso(content.get("CHART_TIME") or content.get("3")),
                "open": float(content.get("OPEN_PRICE") or content.get("4")),
                "high": float(content.get("HIGH_PRICE") or content.get("5")),
                "low": float(content.get("LOW_PRICE") or content.get("6")),
                "close": float(content.get("CLOSE_PRICE") or content.get("7")),
                "volume": int(content.get("VOLUME") or content.get("8")),
            })

    def _dispatch_bar_to_callbacks(self, bar: dict) -> None:
        for cb in self._bar_callbacks:
            try:
                cb(bar)
            except Exception as e:
                logger.error(f"[STREAM] bar callback raised: {e}")

    def _handle_quote(self, msg: dict) -> None:
        for content in msg.get("content", []):
            quote = {
                "symbol": content["key"],
                "bid": float(content.get("BID_PRICE") or content.get("1") or 0),
                "ask": float(content.get("ASK_PRICE") or content.get("2") or 0),
                "last": float(content.get("LAST_PRICE") or content.get("3") or 0),
                "timestamp": _now_iso(),
            }
            for cb in self._quote_callbacks:
                try:
                    cb(quote)
                except Exception as e:
                    logger.error(f"[STREAM] quote callback raised: {e}")

    def _handle_trade_update(self, msg: dict) -> None:
        for cb in self._trade_callbacks:
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"[STREAM] trade callback raised: {e}")


def _ms_to_iso(ms: int | str) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_schwab_stream.py tests/unit/test_bar_aggregator.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_stream.py tests/unit/test_schwab_stream.py
git commit -m "SchwabStreamClient: connect/login + callback dispatch + subscription mgmt"
```

---

### Task 13: Extract NYSE_HOLIDAYS into `market_calendar`

**Files:**
- Create: `src/core/market_calendar.py`
- Create: `tests/unit/test_market_calendar.py`
- Modify: `src/bot/scheduler.py`

- [ ] **Step 1: Find current `NYSE_HOLIDAYS` constant**

```bash
grep -n "NYSE_HOLIDAYS" src/core/tastytrade_client.py
```
Note the constant body (a `frozenset` of `date` objects).

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_market_calendar.py`:
```python
from datetime import date

from src.core.market_calendar import NYSE_HOLIDAYS, is_market_open_day


def test_holidays_includes_known_2026_dates():
    assert date(2026, 1, 1) in NYSE_HOLIDAYS  # New Year's Day
    assert date(2026, 7, 3) in NYSE_HOLIDAYS  # July 4 observed (Friday before)


def test_is_market_open_day_excludes_weekends():
    assert not is_market_open_day(date(2026, 5, 9))  # Saturday
    assert not is_market_open_day(date(2026, 5, 10))  # Sunday
    assert is_market_open_day(date(2026, 5, 11))     # Monday


def test_is_market_open_day_excludes_holidays():
    assert not is_market_open_day(date(2026, 1, 1))
```

- [ ] **Step 3: Run**

```bash
pytest tests/unit/test_market_calendar.py -v
```
Expected: ImportError.

- [ ] **Step 4: Create `src/core/market_calendar.py`** by copying the `NYSE_HOLIDAYS` frozenset from `tastytrade_client.py`, then adding the helper:

```python
"""NYSE holiday calendar and session helpers."""

from datetime import date


NYSE_HOLIDAYS: frozenset[date] = frozenset({
    # paste the existing set body here
})


def is_market_open_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in NYSE_HOLIDAYS
```

- [ ] **Step 5: Update `src/bot/scheduler.py`**

```bash
grep -n "from src.core.tastytrade_client" src/bot/scheduler.py
```
Replace that import with:
```python
from src.core.market_calendar import NYSE_HOLIDAYS
```

- [ ] **Step 6: Run**

```bash
pytest tests/unit/test_market_calendar.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/core/market_calendar.py tests/unit/test_market_calendar.py src/bot/scheduler.py
git commit -m "Extract NYSE_HOLIDAYS into src/core/market_calendar"
```

---

## Phase 4 — Order executor + dry-run

### Task 14: Rewrite OrderExecutor against SchwabClient

**Files:**
- Modify: `src/core/order_executor.py`
- Modify: `tests/unit/test_order_executor*.py` (existing tests will need adjustment)

- [ ] **Step 1: Inspect existing executor and list the call-site translation table**

```bash
grep -nE "self\.client\." src/core/order_executor.py
```

Expected mapping (apply each as you encounter it):
| Tastytrade call                     | SchwabClient call                              |
| ----------------------------------- | ---------------------------------------------- |
| `self.client.submit_market_order`   | `self.client.submit_market_order` (same)       |
| `self.client.submit_limit_order`    | `self.client.submit_limit_order` (same)        |
| `self.client.submit_stop_limit_order` | `self.client.submit_stop_limit_order` (same) |
| `self.client.cancel_order`          | `self.client.cancel_order` (same)              |
| `self.client.get_orders`            | `self.client.get_orders` (same)                |
| `self.client.get_order`             | `self.client.get_order` (same)                 |
| `self.client._build_equity_leg`     | (DELETE — no longer needed; SchwabClient builds internally) |
| `self.client._map_side_to_action`   | (DELETE — same reason)                          |

- [ ] **Step 2: Edit imports at top of `src/core/order_executor.py`**

Replace:
```python
from src.core.tastytrade_client import TastytradeClient
```
with:
```python
from src.core.schwab_client import SchwabClient
```

Update the `__init__` parameter type:
```python
    def __init__(self, client: SchwabClient, ...):
```

- [ ] **Step 3: Apply the mapping**

For every line returned by step 1's grep, apply the matching row from the table. Delete any line that calls into a `_build_equity_leg` or `_map_side_to_action` helper — SchwabClient's order methods accept `symbol`, `qty`, `side` directly.

- [ ] **Step 4: Run the existing executor tests**

```bash
pytest tests/unit/test_order_executor*.py -v
```
Expected: passed. If a failure surfaces a tastytrade-specific error string or exception class still being caught, replace it with a generic `RuntimeError` or `httpx.HTTPStatusError`.

- [ ] **Step 5: Commit**

```bash
git add src/core/order_executor.py tests/unit/test_order_executor*.py
git commit -m "Rewire OrderExecutor on top of SchwabClient"
```

---

### Task 15: Dry-run intercept (entry + exit fabrication)

**Files:**
- Modify: `src/core/order_executor.py`
- Create: `tests/unit/test_dry_run_executor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dry_run_executor.py`:
```python
from unittest.mock import MagicMock

from config.settings import TradingMode
from src.core.order_executor import OrderExecutor


def test_dry_run_market_buy_does_not_call_place_order():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.get_latest_price.return_value = 10.50

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.DRY_RUN)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="buy")

    assert result.success is True
    assert result.filled_qty == 5
    assert result.filled_price == 10.50
    assert getattr(result, "dry_run", False) is True
    schwab.submit_market_order.assert_not_called()


def test_dry_run_market_sell_uses_current_quote_for_exit_fill():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.get_latest_price.return_value = 12.00

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.DRY_RUN)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="sell")

    assert result.success is True
    assert result.filled_price == 12.00
    schwab.submit_market_order.assert_not_called()


def test_live_mode_calls_place_order():
    schwab = MagicMock()
    schwab.is_authenticated = True
    schwab.submit_market_order.return_value = "ORD-1"
    schwab.get_order.return_value = {
        "id": "ORD-1", "status": "filled", "qty": 5, "filled_qty": 5,
        "type": "market", "price": None, "stop_price": None,
        "submitted_at": "2026-05-08T13:30:00+0000", "symbol": "AAPL",
    }

    ex = OrderExecutor(client=schwab, trading_mode=TradingMode.LIVE)
    result = ex.execute_market_order(symbol="AAPL", qty=5, side="buy")

    assert schwab.submit_market_order.called
    assert result.success is True
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_dry_run_executor.py -v
```
Expected: failures (`trading_mode` parameter not accepted, no `dry_run` attribute on result).

- [ ] **Step 3: Edit `OrderExecutor`**

In `__init__`, add a `trading_mode` parameter (default `TradingMode.LIVE` for backwards compatibility within callers — `main.py` will pass it from config):
```python
from config.settings import TradingMode
# ...
    def __init__(
        self,
        client: SchwabClient,
        trading_mode: TradingMode = TradingMode.LIVE,
        ...,
    ):
        self.client = client
        self.trading_mode = trading_mode
```

In the `OrderResult` dataclass, add `dry_run: bool = False`.

At the very top of `_submit_order` (or whichever method actually calls `client.submit_market_order`), inject the intercept:
```python
    def _submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        **kwargs,
    ) -> OrderResult:
        if self.trading_mode == TradingMode.DRY_RUN:
            price = self.client.get_latest_price(symbol)
            return OrderResult(
                success=True,
                order_id=f"DRYRUN-{symbol}-{datetime.utcnow().isoformat()}",
                symbol=symbol,
                qty=qty,
                filled_qty=qty,
                filled_price=price,
                status="filled",
                error=None,
                dry_run=True,
            )
        # ...existing live path...
```

Make sure the same intercept fires for `execute_limit_order` and `execute_stop_limit_order` — easiest is to centralize through `_submit_order` if not already, or duplicate the check.

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_dry_run_executor.py tests/unit/test_order_executor*.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/order_executor.py tests/unit/test_dry_run_executor.py
git commit -m "Dry-run intercept: fabricate fills at current quote, no orders sent"
```

---

## Phase 5 — ORB strategy + wiring

### Task 16: Implement `OpeningRangeBreakout` strategy

**Files:**
- Create: `src/bot/signals/orb.py`
- Create: `tests/unit/test_orb_strategy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_orb_strategy.py`:
```python
from datetime import datetime, time, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.bot.signals.orb import OpeningRangeBreakout
from src.bot.signals.base import SignalDirection


def _bars_df(rows):
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp")


def make_or_bars():
    """Three 5-min bars: 9:30, 9:35, 9:40 — total volume 6000, high=10.5, low=9.8."""
    return _bars_df([
        {"timestamp": "2026-05-08T13:30:00Z", "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.3, "volume": 2000},
        {"timestamp": "2026-05-08T13:35:00Z", "open": 10.3, "high": 10.5, "low": 9.8, "close": 10.0, "volume": 2000},
        {"timestamp": "2026-05-08T13:40:00Z", "open": 10.0, "high": 10.45, "low": 9.95, "close": 10.4, "volume": 2000},
    ])


def test_lock_or_records_high_low_and_volume():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    state = strat.state["AAPL"]
    assert state.or_high == 10.5
    assert state.or_low == 9.8
    assert state.or_volume == 6000
    assert state.or_locked is True


def test_breakout_emits_long_signal():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())

    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 2500}
    sig = strat.on_bar(bar)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.entry_price == 10.6
    assert sig.stop_price == 9.8
    assert sig.target_price == pytest.approx(10.6 + 2 * (10.6 - 9.8))
    assert sig.strategy == "orb"


def test_no_signal_when_volume_too_low():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())

    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 100}  # below 6000/3
    assert strat.on_bar(bar) is None


def test_no_signal_when_close_below_or_high():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.49, "low": 10.4, "close": 10.49, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_does_not_fire_twice():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    bar1 = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
            "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    bar2 = {"symbol": "AAPL", "timestamp": "2026-05-08T13:55:00Z",
            "open": 10.6, "high": 10.8, "low": 10.55, "close": 10.75, "volume": 3000}
    assert strat.on_bar(bar1) is not None
    assert strat.on_bar(bar2) is None


def test_no_signal_when_or_not_locked():
    strat = OpeningRangeBreakout()
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T13:50:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_late_day_cutoff_blocks_entries_after_1515_et():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    # 19:20 UTC = 15:20 ET (DST)
    bar = {"symbol": "AAPL", "timestamp": "2026-05-08T19:20:00Z",
           "open": 10.4, "high": 10.7, "low": 10.4, "close": 10.6, "volume": 3000}
    assert strat.on_bar(bar) is None


def test_reset_clears_all_state():
    strat = OpeningRangeBreakout()
    strat.lock_or("AAPL", make_or_bars())
    strat.reset()
    assert strat.state == {}
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_orb_strategy.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/bot/signals/orb.py`**

```python
"""
Opening Range Breakout — long-only strategy.

Locks the 9:30-9:45 ET range from REST pricehistory at 9:45:30 ET, then watches
streaming 5-min bars for a close above the OR high (with bar volume >= 1/3 of
OR volume). Emits a single Signal per symbol per day; exit logic is handled
by monitor.py + position_manager.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

import pandas as pd
import pytz

from src.bot.signals.base import Signal, SignalDirection, SignalGenerator


ET = pytz.timezone("America/New_York")
ENTRY_CUTOFF_ET = time(15, 15)


@dataclass
class _ORState:
    or_high: float = 0.0
    or_low: float = 0.0
    or_volume: int = 0
    or_locked: bool = False
    breakout_fired: bool = False


class OpeningRangeBreakout(SignalGenerator):
    """ORB-15 (9:30-9:45 ET) strategy."""

    def __init__(self, *, target_r: float = 2.0):
        self.target_r = target_r
        self.state: dict[str, _ORState] = {}

    def register(self, symbol: str) -> None:
        self.state.setdefault(symbol, _ORState())

    def lock_or(self, symbol: str, or_bars: pd.DataFrame) -> None:
        st = self.state.setdefault(symbol, _ORState())
        if or_bars.empty:
            return
        st.or_high = float(or_bars["high"].max())
        st.or_low = float(or_bars["low"].min())
        st.or_volume = int(or_bars["volume"].sum())
        st.or_locked = True

    def on_bar(self, bar: dict) -> Optional[Signal]:
        symbol = bar["symbol"]
        st = self.state.get(symbol)
        if st is None or not st.or_locked or st.breakout_fired:
            return None

        ts = datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00"))
        if ts.astimezone(ET).time() >= ENTRY_CUTOFF_ET:
            return None

        if bar["close"] <= st.or_high:
            return None
        if bar["volume"] < st.or_volume / 3:
            return None

        entry = float(bar["close"])
        risk = entry - st.or_low
        target = entry + self.target_r * risk
        st.breakout_fired = True

        return Signal(
            symbol=symbol,
            direction=SignalDirection.LONG,
            entry_price=entry,
            stop_price=st.or_low,
            target_price=target,
            strategy="orb",
            timeframe="5Min",
            metadata={
                "or_high": st.or_high,
                "or_low": st.or_low,
                "or_volume": st.or_volume,
                "breakout_volume": int(bar["volume"]),
            },
        )

    def reset(self) -> None:
        self.state.clear()

    # SignalGenerator compat — ORB is event-driven via on_bar, not generate()
    def generate(self, symbol: str, bars, current_price: float) -> Optional[Signal]:
        return None
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_orb_strategy.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/bot/signals/orb.py tests/unit/test_orb_strategy.py
git commit -m "Add OpeningRangeBreakout strategy"
```

---

### Task 17: Wire SchwabClient + ORB into `main.py`

**Files:**
- Modify: `src/bot/main.py`
- Modify: `src/bot/monitor.py`
- Modify: `src/bot/stream_handler.py`

This is a single integration commit — there's no clean way to split it because the imports cross-depend.

- [ ] **Step 1: Replace top-of-file imports in `src/bot/main.py`**

Remove:
```python
from src.core.tastytrade_client import TastytradeClient
from src.core.tastytrade_ws import TastytradeWSClient
from src.bot.signals.momentum_pullback import MomentumPullbackStrategy
from src.bot.signals.momentum_surge import MomentumSurgeStrategy
from src.bot.press_release_scanner import PressReleaseScanner
from src.core.regime_detector import RegimeDetector
```

Add:
```python
from src.core.schwab_client import SchwabClient
from src.core.schwab_stream import SchwabStreamClient
from src.bot.signals.orb import OpeningRangeBreakout
```

- [ ] **Step 2: Replace the `__init__` body of `TradingBot`**

Replace the broker / strategy / press-release / regime sections with:
```python
        # Schwab broker
        self.client = SchwabClient(
            app_key=self.config.schwab_app_key,
            app_secret=self.config.schwab_app_secret,
            callback_url=self.config.schwab_oauth_redirect_uri,
            token_path=self.config.schwab_token_path,
            pinned_account_hash=self.config.schwab_account_hash,
        )

        # ORB strategy
        self.strategy = OpeningRangeBreakout(target_r=self.config.risk_reward_target)

        # Order executor with dry-run mode
        self.order_executor = OrderExecutor(
            client=self.client,
            trading_mode=self.config.trading_mode,
        )

        # Stream client (Schwab WebSocket)
        self.ws_client = SchwabStreamClient(schwab_client=self.client)
```

Delete:
- `self.regime_detector = ...`
- `self.press_release_scanner = ...`
- `self.surge_strategy = MomentumSurgeStrategy(...)`
- `self.strategy = MomentumPullbackStrategy(...)` (now replaced by ORB above)
- the strategies dict in stream handler init — pass `{"orb": self.strategy}` instead

Update `StreamHandler` construction:
```python
        self.stream_handler = StreamHandler(
            strategy=self.strategy,
            processor=self.processor,
            executor=self.executor,
            monitor=self.monitor,
            position_manager=self.position_manager,
            portfolio_limits=self.portfolio_limits,
            bot_state=self.bot_state,
            client=self.client,
            ws_client=self.ws_client,
            config=self.config,
            strategies={"orb": self.strategy},
        )
```

Delete the `_run_press_release_scan`, `_refresh_gap_up_anchors` (still useful — keep), `regime_detector.refresh()` lines. Strip every reference to `self.regime_detector` and `self.press_release_scanner`.

- [ ] **Step 3: Edit `src/bot/monitor.py`** — change one import:

Replace:
```python
from src.core.tastytrade_client import TastytradeClient
```
with:
```python
from src.core.schwab_client import SchwabClient
```

Update the constructor type hint accordingly (`client: SchwabClient`).

- [ ] **Step 4: Edit `src/bot/stream_handler.py`**

Replace imports:
```python
from src.core.tastytrade_client import TastytradeClient   # remove
from src.core.tastytrade_ws import TastytradeWSClient     # remove
```
with:
```python
from src.core.schwab_client import SchwabClient
from src.core.schwab_stream import SchwabStreamClient
```

Update type hints accordingly (`client: SchwabClient`, `ws_client: SchwabStreamClient`).

Find and remove these references (use grep to locate, then delete):
```bash
grep -nE "momentum_surge|momentum_pullback|regime_detector|has_catalyst|news_headline|news_count|news_source" src/bot/stream_handler.py
```
For each hit:
- if it's an import statement: delete the line
- if it's a payload field assignment (e.g. `metadata["has_catalyst"]`): delete the line
- if it's a key lookup in a dict comprehension or to_dict(): delete the entry

Also delete the entire `_run_press_release_scan` method from `main.py` (it's no longer in `set_callbacks`).

- [ ] **Step 5: Run the test suite**

```bash
pytest tests/unit -v
```
Expected: every existing test still passes, no new tests broken by the wiring.

- [ ] **Step 6: Commit**

```bash
git add src/bot/main.py src/bot/monitor.py src/bot/stream_handler.py
git commit -m "Wire SchwabClient + SchwabStreamClient + ORB into TradingBot"
```

---

### Task 18: Schedule the 9:45:30 ET OR-lock job

**Files:**
- Modify: `src/bot/scheduler.py`
- Modify: `src/bot/main.py`

- [ ] **Step 1: Add an `or_lock` job to `BotScheduler`**

Find the `set_callbacks` method and add `or_lock` to its parameters:
```python
    def set_callbacks(
        self,
        momentum_scan,
        end_of_day,
        daily_reset,
        broker_sync,
        or_lock,
    ):
        ...
        self._or_lock = or_lock
```

In the scheduler `start()` method, add a cron job:
```python
        self._scheduler.add_job(
            self._or_lock,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=9, minute=45, second=30,
                timezone=pytz.timezone("America/New_York"),
            ),
            id="or_lock",
            name="ORB: lock 9:30-9:45 range",
            replace_existing=True,
        )
```

(Adjust the import for `CronTrigger` and `pytz` if not already imported.)

- [ ] **Step 2: Implement `_lock_opening_ranges` in `main.py`**

```python
    async def _lock_opening_ranges(self) -> None:
        """At 9:45:30 ET, fetch the 9:30-9:45 window for each watchlist symbol."""
        if not self._running:
            return
        symbols = [c.symbol for c in self._scanner_results] + sorted(self._gap_up_anchors)
        symbols = list({s for s in symbols if s})  # dedupe + drop empties
        if not symbols:
            logger.info("[OR-LOCK] No symbols on watchlist; skipping.")
            return

        for symbol in symbols:
            try:
                bars = self.client.get_bars(symbol, timeframe="5Min", limit=3)
                if bars.empty:
                    logger.warning(f"[OR-LOCK] {symbol}: no bars returned")
                    continue
                self.strategy.lock_or(symbol, bars)
                st = self.strategy.state[symbol]
                logger.info(
                    f"[OR-LOCK] {symbol}: H=${st.or_high:.2f} L=${st.or_low:.2f} "
                    f"V={st.or_volume:,}"
                )
            except Exception as e:
                logger.error(f"[OR-LOCK] {symbol} failed: {e}")
```

Wire it up in the scheduler `set_callbacks` invocation:
```python
        self.scheduler.set_callbacks(
            momentum_scan=self._run_momentum_scan,
            end_of_day=self._end_of_day_cleanup,
            daily_reset=self._daily_reset,
            broker_sync=self._sync_with_broker,
            or_lock=self._lock_opening_ranges,
        )
```

In `_daily_reset`, also call `self.strategy.reset()`.

- [ ] **Step 3: Smoke-import to verify nothing crashes**

```bash
python -c "from src.bot.main import TradingBot; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Run unit tests**

```bash
pytest tests/unit -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/bot/scheduler.py src/bot/main.py
git commit -m "Schedule 9:45:30 ET OR-lock job; wire into ORB strategy"
```

---

## Phase 6 — Web / OAuth / Dashboard

### Task 19: Create `src/bot/web.py` skeleton with Schwab OAuth routes

**Files:**
- Create: `src/bot/web.py`
- Create: `tests/unit/test_oauth_routes.py`

The previous (surge-era) `web.py` was discarded along with the rest of the surge working tree. This task scaffolds a fresh `web.py` containing only the FastAPI app, the bot wiring (`set_bot`), and the three OAuth routes Schwab needs. Task 20 expands it with the dashboard HTML and `/api/*` endpoints.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_oauth_routes.py`:
```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bot import web


@pytest.fixture
def client_with_bot():
    bot = MagicMock()
    bot.config.schwab_app_key = "K"
    bot.config.schwab_oauth_redirect_uri = "https://ut.gitsum.rest/schwab/oauth/callback"
    bot.config.schwab_app_secret = "S"
    bot.config.schwab_token_path = "/tmp/token.json"
    bot.client.is_authenticated = False
    bot.client.account_hash = None
    web.set_bot(bot)
    yield TestClient(web.app), bot


def test_oauth_start_redirects_to_schwab_authorize(client_with_bot):
    client, bot = client_with_bot
    resp = client.get("/schwab/oauth/start", follow_redirects=False)
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://api.schwabapi.com/v1/oauth/authorize")
    assert "client_id=K" in location
    assert "redirect_uri=https" in location


def test_oauth_callback_persists_token_and_reloads_client(client_with_bot):
    client, bot = client_with_bot
    with patch("src.bot.web.client_from_received_url") as cfru:
        cfru.return_value = MagicMock()
        resp = client.get(
            "/schwab/oauth/callback",
            params={"code": "ABC", "session": "S"},
            follow_redirects=False,
        )
    assert resp.status_code in (200, 302, 307)
    cfru.assert_called_once()
    bot.client.reload_from_disk.assert_called_once()


def test_legacy_oauth_callback_returns_410(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/oauth/callback")
    assert resp.status_code == 410


def test_root_returns_html(client_with_bot):
    client, _ = client_with_bot
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_oauth_routes.py -v
```
Expected: ImportError on `from src.bot import web`.

- [ ] **Step 3: Create `src/bot/web.py` from scratch**

```python
"""
Bot dashboard + OAuth callback server.

FastAPI app. Schwab OAuth flow at /schwab/oauth/{start,callback}.
Dashboard HTML and /api/* endpoints are added in Task 20.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from schwab.auth import client_from_received_url
except ImportError:  # pragma: no cover
    client_from_received_url = None


app = FastAPI(title="sgt-schwab", version="1.0.0")

_bot = None


def set_bot(bot) -> None:
    """Called by run_bot.py at startup to give the API access to the bot."""
    global _bot
    _bot = bot


_HTML_STUB = """\
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>sgt-schwab</title></head>
<body><h1>sgt-schwab</h1><p>Dashboard rendering moves to Task 20.</p></body>
</html>
"""


@app.get("/")
async def dashboard():
    return HTMLResponse(_HTML_STUB)


@app.get("/schwab/oauth/start")
async def schwab_oauth_start():
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    params = {
        "client_id": _bot.config.schwab_app_key,
        "redirect_uri": _bot.config.schwab_oauth_redirect_uri,
        "response_type": "code",
    }
    url = "https://api.schwabapi.com/v1/oauth/authorize?" + urlencode(params)
    return RedirectResponse(url, status_code=307)


@app.get("/schwab/oauth/callback")
async def schwab_oauth_callback(request: Request):
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    full_url = str(request.url)
    try:
        client_from_received_url(
            api_key=_bot.config.schwab_app_key,
            app_secret=_bot.config.schwab_app_secret,
            received_url=full_url,
            token_path=_bot.config.schwab_token_path,
        )
    except Exception as e:
        raise HTTPException(400, f"OAuth exchange failed: {e}")
    _bot.client.reload_from_disk()
    return RedirectResponse("/", status_code=302)


@app.get("/oauth/authorize")
@app.get("/oauth/callback")
async def _legacy_oauth_410():
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy OAuth path. Use /schwab/oauth/start to begin the Schwab "
            "authorize flow; the callback URL is /schwab/oauth/callback."
        ),
    )
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/test_oauth_routes.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/bot/web.py tests/unit/test_oauth_routes.py
git commit -m "web.py: skeleton + /schwab/oauth/{start,callback} routes"
```

---

### Task 20: Add /api/* endpoints + ORB dashboard HTML

**Files:**
- Modify: `src/bot/web.py`
- Create: `tests/unit/test_dashboard_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dashboard_endpoints.py`:
```python
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.bot import web
from src.bot.signals.orb import _ORState


@pytest.fixture
def bot_app():
    bot = MagicMock()
    bot.client.is_authenticated = True
    bot.client.account_hash = "HASH-AAA"
    bot.client.get_account.return_value = {
        "equity": 270.0, "buying_power": 250.0, "cash": 250.0,
        "daytrade_count": 0, "is_pdt": False, "type": "CASH", "status": "active",
    }
    bot.config.schwab_app_key = "K"
    bot.config.trading_mode.value = "dry_run"
    bot.strategy.state = {
        "AAPL": _ORState(or_high=10.5, or_low=9.8, or_volume=6000,
                         or_locked=True, breakout_fired=False),
    }
    bot.position_manager.get_open_positions.return_value = []
    bot._scanner_results = []
    web.set_bot(bot)
    yield TestClient(web.app), bot


def test_auth_status(bot_app):
    client, _ = bot_app
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {
        "authenticated": True, "account_hash": "HASH-AAA", "broker": "schwab",
    }


def test_orb_state(bot_app):
    client, _ = bot_app
    r = client.get("/api/orb")
    assert r.status_code == 200
    payload = r.json()
    assert "AAPL" in payload
    assert payload["AAPL"]["or_high"] == 10.5
    assert payload["AAPL"]["or_locked"] is True


def test_status_returns_account_when_authenticated(bot_app):
    client, _ = bot_app
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["equity"] == 270.0
    assert body["trading_mode"] == "dry_run"


def test_status_returns_setup_mode_when_unauthenticated():
    bot = MagicMock()
    bot.client.is_authenticated = False
    web.set_bot(bot)
    r = TestClient(web.app).get("/api/status")
    assert r.status_code == 200
    assert r.json()["mode"] == "setup"


def test_dashboard_html_mentions_orb(bot_app):
    client, _ = bot_app
    r = client.get("/")
    assert r.status_code == 200
    assert "ORB" in r.text or "Opening Range" in r.text
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/test_dashboard_endpoints.py -v
```
Expected: failures (endpoints don't exist; HTML stub doesn't mention ORB).

- [ ] **Step 3: Append the API endpoints to `src/bot/web.py`**

After the OAuth routes, add:
```python
@app.get("/api/auth/status")
async def auth_status() -> dict:
    if _bot is None:
        return {"authenticated": False, "account_hash": None, "broker": "schwab"}
    return {
        "authenticated": _bot.client.is_authenticated,
        "account_hash": _bot.client.account_hash,
        "broker": "schwab",
    }


@app.get("/api/status")
async def status() -> dict:
    if _bot is None or not _bot.client.is_authenticated:
        return {"mode": "setup", "authenticated": False}
    try:
        account = _bot.client.get_account()
    except Exception as e:
        return {"mode": "error", "error": str(e)}
    return {
        "mode": "running",
        "authenticated": True,
        "account": account,
        "trading_mode": str(_bot.config.trading_mode.value),
    }


@app.get("/api/orb")
async def orb_state() -> dict:
    if _bot is None:
        return {}
    return {
        sym: {
            "or_high": st.or_high,
            "or_low": st.or_low,
            "or_volume": st.or_volume,
            "or_locked": st.or_locked,
            "breakout_fired": st.breakout_fired,
        }
        for sym, st in _bot.strategy.state.items()
    }


@app.get("/api/positions")
async def positions() -> list[dict]:
    if _bot is None:
        return []
    return [p.to_dict() for p in _bot.position_manager.get_open_positions()]


@app.get("/api/scanner")
async def scanner() -> list[dict]:
    if _bot is None:
        return []
    return [
        {"symbol": c.symbol, "price": c.price, "change_pct": c.change_pct}
        for c in _bot._scanner_results[:20]
    ]
```

- [ ] **Step 4: Replace `_HTML_STUB` and the `dashboard()` function**

Delete the existing `_HTML_STUB` constant and the `dashboard()` function. Replace with:
```python
_DASHBOARD_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>sgt-schwab - ORB</title>
  <style>
    body { font-family: ui-monospace, monospace; background: #0e1117; color: #c9d1d9; margin: 0; padding: 24px; }
    h1 { font-size: 18px; margin: 0 0 16px; }
    .panel { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
    th { color: #8b949e; font-weight: normal; }
    .ok { color: #3fb950; }
    .warn { color: #d29922; }
    .err { color: #f85149; }
    button { background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-family: inherit; }
  </style>
</head>
<body>
  <h1>sgt-schwab - ORB (Opening Range Breakout)</h1>

  <div class="panel">
    <strong>Auth:</strong> <span id="auth"></span>
    <span style="margin-left: 16px;"><strong>Mode:</strong> <span id="mode"></span></span>
    <button id="oauth-btn" style="margin-left: 16px; display:none">Authorize Schwab</button>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Account</h2>
    <div id="account"></div>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">ORB state</h2>
    <table id="orb-table"><thead><tr>
      <th>Symbol</th><th>OR High</th><th>OR Low</th><th>OR Vol</th><th>Locked</th><th>Fired</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Positions</h2>
    <table id="pos-table"><thead><tr>
      <th>Symbol</th><th>Qty</th><th>Entry</th><th>Now</th><th>P&amp;L</th>
    </tr></thead><tbody></tbody></table>
  </div>

<script>
function setText(id, value, cls) {
  const el = document.getElementById(id);
  el.textContent = value;
  if (cls !== undefined) el.className = cls;
}

function makeCell(text, cls) {
  const td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

function renderTable(tbodySelector, rows) {
  const tbody = document.querySelector(tbodySelector);
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  for (const cells of rows) {
    const tr = document.createElement('tr');
    for (const c of cells) tr.appendChild(makeCell(c.text, c.cls));
    tbody.appendChild(tr);
  }
}

async function refresh() {
  const auth = await (await fetch('/api/auth/status')).json();
  setText('auth', auth.authenticated ? 'authenticated' : 'unauthenticated',
          auth.authenticated ? 'ok' : 'err');
  document.getElementById('oauth-btn').style.display =
      auth.authenticated ? 'none' : 'inline-block';

  const status = await (await fetch('/api/status')).json();
  setText('mode', status.mode || '-');
  if (status.account) {
    setText('account',
      'Equity: $' + status.account.equity.toFixed(2)
      + ' | BP: $' + status.account.buying_power.toFixed(2)
      + ' | Cash: $' + status.account.cash.toFixed(2)
      + ' | DT count: ' + status.account.daytrade_count);
  } else {
    setText('account', '-');
  }

  const orb = await (await fetch('/api/orb')).json();
  const orbRows = Object.entries(orb).map(function (entry) {
    const sym = entry[0]; const st = entry[1];
    return [
      {text: sym},
      {text: '$' + st.or_high.toFixed(2)},
      {text: '$' + st.or_low.toFixed(2)},
      {text: st.or_volume.toLocaleString()},
      {text: st.or_locked ? 'YES' : 'no', cls: st.or_locked ? 'ok' : 'warn'},
      {text: st.breakout_fired ? 'YES' : 'no', cls: st.breakout_fired ? 'ok' : ''},
    ];
  });
  renderTable('#orb-table tbody', orbRows);

  const positions = await (await fetch('/api/positions')).json();
  const posRows = positions.map(function (p) {
    const pnl = p.unrealized_pnl || 0;
    return [
      {text: p.symbol},
      {text: String(p.qty)},
      {text: '$' + (p.entry_price || 0).toFixed(2)},
      {text: '$' + (p.current_price || 0).toFixed(2)},
      {text: '$' + pnl.toFixed(2), cls: pnl >= 0 ? 'ok' : 'err'},
    ];
  });
  renderTable('#pos-table tbody', posRows);
}

document.getElementById('oauth-btn').addEventListener('click', function () {
  window.location = '/schwab/oauth/start';
});

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


@app.get("/")
async def dashboard():
    return HTMLResponse(_DASHBOARD_HTML)
```

- [ ] **Step 5: Run**

```bash
pytest tests/unit/test_dashboard_endpoints.py tests/unit/test_oauth_routes.py -v
```
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/bot/web.py tests/unit/test_dashboard_endpoints.py
git commit -m "web.py: /api/* endpoints + ORB dashboard HTML"
```

---

## Phase 7 — Deploy

### Task 21: Update deploy script and Caddy notes

**Files:**
- Modify: `deploy/deploy-remote.sh`

- [ ] **Step 1: Replace surge paths and container names**

```bash
sed -i.bak 's|/opt/sgt-surge/|/opt/sgt-schwab/|g; s|sgt-surge|sgt-schwab|g' deploy/deploy-remote.sh
rm deploy/deploy-remote.sh.bak
```

Inspect the result:
```bash
grep -nE "(sgt-surge|sgt-schwab)" deploy/deploy-remote.sh
```

- [ ] **Step 2: Add token-volume mount**

Find the `podman run` (or compose) invocation inside `deploy-remote.sh` and ensure it includes:
```
-v /opt/sgt-schwab/state:/app/state:Z
```
so `state/schwab_token.json` survives image rebuilds.

- [ ] **Step 3: Add a deploy-time README note**

Append to `README.md` deployment section:
```
The Schwab OAuth callback is path-scoped: register
`https://ut.gitsum.rest/schwab/oauth/callback` in the Schwab developer portal.
No DNS or Caddy changes are required — the existing `ut.gitsum.rest` block
proxies `/schwab/oauth/*` to the bot on port 8080.
```

- [ ] **Step 4: Commit**

```bash
git add deploy/deploy-remote.sh README.md
git commit -m "Deploy: rename paths sgt-surge -> sgt-schwab; mount state volume"
```

---

## Phase 8 — Smoke + cutover

### Task 22: Implement `scripts/smoke_schwab.py`

**Files:**
- Create: `scripts/smoke_schwab.py`

- [ ] **Step 1: Write the smoke script**

```python
#!/usr/bin/env python3
"""
Smoke test for the Schwab integration. Run against live Schwab in dry_run mode.

Verifies: auth, account hash, pricehistory pull, streaming bar callback,
synthetic ORB signal -> dry-run fill -> exit fill.
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from loguru import logger

from config.settings import TradingMode
from src.bot.config import get_bot_config
from src.core.schwab_client import SchwabClient
from src.core.schwab_stream import SchwabStreamClient
from src.core.order_executor import OrderExecutor
from src.bot.signals.orb import OpeningRangeBreakout
from src.bot.signals.base import Signal, SignalDirection


async def main() -> int:
    cfg = get_bot_config()
    if cfg.trading_mode != TradingMode.DRY_RUN:
        logger.error("Refusing to run smoke test in live mode")
        return 1

    client = SchwabClient(
        app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri,
        token_path=cfg.schwab_token_path,
        pinned_account_hash=cfg.schwab_account_hash,
    )

    logger.info("--- 1. Auth check ---")
    assert client.is_authenticated, "Not authenticated — run OAuth via dashboard first"
    logger.info(f"  account_hash = {client.account_hash}")

    logger.info("--- 2. pricehistory pull ---")
    bars = client.get_bars("SPY", timeframe="5Min", limit=10)
    assert not bars.empty, "Schwab returned no bars for SPY"
    logger.info(f"  got {len(bars)} 5-min bars; last close ${bars['close'].iloc[-1]:.2f}")

    logger.info("--- 3. Streaming bar callback ---")
    stream = SchwabStreamClient(schwab_client=client)
    bar_count = {"n": 0}
    stream.on_bar(lambda b: bar_count.update(n=bar_count["n"] + 1))

    ok = await stream.connect_data()
    assert ok, "connect_data failed"
    await stream.subscribe(bars=["SPY"], quotes=["SPY"])

    async def loop():
        try:
            await asyncio.wait_for(stream.run_data_loop(), timeout=90)
        except asyncio.TimeoutError:
            pass

    await loop()
    logger.info(f"  received {bar_count['n']} bars in 90s")

    logger.info("--- 4. Dry-run executor round-trip ---")
    ex = OrderExecutor(client=client, trading_mode=TradingMode.DRY_RUN)
    entry_price = client.get_latest_price("SPY")
    entry = ex.execute_market_order("SPY", qty=1, side="buy")
    assert entry.success and entry.dry_run
    logger.info(f"  entry: dry-run fill at ${entry.filled_price:.2f}")

    exit_ = ex.execute_market_order("SPY", qty=1, side="sell")
    assert exit_.success and exit_.dry_run
    logger.info(f"  exit:  dry-run fill at ${exit_.filled_price:.2f}")

    await stream.disconnect()
    logger.info("--- All smoke checks passed ---")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Run it (only meaningful with valid Schwab token + during market hours)**

```bash
python scripts/smoke_schwab.py
```
Expected: each step logs success; exit code 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_schwab.py
git commit -m "Add Schwab smoke test (dry-run only)"
```

---

### Task 23: Final cleanup and tastytrade file removal

**Files:**
- Delete: `src/core/tastytrade_client.py`
- Delete: `src/core/tastytrade_ws.py`
- Modify: `scripts/run_bot.py`

- [ ] **Step 1: Strip surge-specific CLI from `scripts/run_bot.py`**

Open `scripts/run_bot.py` and:
- Remove the `--check-signals` argparse argument and the `check_signals_once()` function (it imports `MomentumSurgeStrategy`, which is deleted).
- Update the `--status` path's import: `from src.core.tastytrade_client import TastytradeClient` → `from src.core.schwab_client import SchwabClient`. Replace the constructor call to use the BotConfig fields:
  ```python
  cfg = get_bot_config()
  client = SchwabClient(
      app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
      callback_url=cfg.schwab_oauth_redirect_uri,
      token_path=cfg.schwab_token_path,
      pinned_account_hash=cfg.schwab_account_hash,
  )
  ```
- The `--dry-run` argparse flag (which prints the config) needs no changes; it doesn't touch the client.

- [ ] **Step 2: Confirm no remaining importers**

```bash
grep -rnE "tastytrade_(client|ws)" --include="*.py" src/ scripts/ tests/
```
Expected: empty (or only the files about to be deleted).

- [ ] **Step 3: Delete the files**

```bash
git rm src/core/tastytrade_client.py src/core/tastytrade_ws.py
```

- [ ] **Step 4: Run the full suite**

```bash
pytest tests/ -v
```
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_bot.py
git commit -m "Remove tastytrade modules + scrub run_bot CLI of surge-only flags"
```

---

### Task 24: Cutover — deploy to ut.gitsum.rest

This is operator work, not code. Document the steps so the engineer running the migration knows exactly what to do.

- [ ] **Step 1: Register the Schwab developer app**

Visit https://developer.schwab.com → create app → set callback URL to exactly:
```
https://ut.gitsum.rest/schwab/oauth/callback
```
Note the `app_key` and `app_secret`.

- [ ] **Step 2: Set env vars on the server**

SSH to `jacisjake@ut.gitsum.rest`:
```bash
sudo nano /opt/sgt-schwab/.env
```
Paste the new `.env.example` template, fill in `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`. Confirm `TRADING_MODE=dry_run`.

- [ ] **Step 3: Deploy the cleaning branch**

From your local machine:
```bash
cd deploy && ./deploy-remote.sh jacisjake@ut.gitsum.rest --build
```

- [ ] **Step 4: Complete OAuth from the browser**

Visit https://ut.gitsum.rest in a browser. Click "Authorize Schwab". Complete the Schwab OAuth flow. The callback writes `state/schwab_token.json`. The dashboard's auth status flips to authenticated.

- [ ] **Step 5: Watch dry-run for at least one full trading day**

Verify in the logs:
- 9:25 ET: scanner finds gappers
- 9:45:30 ET: `[OR-LOCK]` lines for each watchlist symbol
- During session: any `[SIGNAL]` + dry-run fill messages
- 15:55 ET: EOD safety net runs

Read the trade ledger; verify it matches what you'd expect to have happened.

- [ ] **Step 6: Run the smoke script on the server**

```bash
ssh jacisjake@ut.gitsum.rest "podman exec sgt-schwab python scripts/smoke_schwab.py"
```
Expected: exit 0.

- [ ] **Step 7: After three clean dry-run days, flip to live**

Edit `/opt/sgt-schwab/.env` on the server: `TRADING_MODE=live`. Restart the container:
```bash
ssh jacisjake@ut.gitsum.rest "podman restart sgt-schwab"
```

First live day starts with $270.

---

## Self-Review checklist

(Verify before handing off to executor.)

- [x] Every spec section has a task that implements it (broker swap → Tasks 6–10; streaming → 11–13; executor + dry-run → 14–15; ORB → 16; wiring → 17–18; web/OAuth → 19–20; deploy → 21; testing → 22; cutover → 24).
- [x] No "TBD" / "TODO" / "implement later" placeholders.
- [x] Every code-touching step shows the actual code or precise grep/sed command.
- [x] Method names are consistent across tasks (e.g. `get_bars`, `submit_market_order`, `lock_or`, `on_bar`, `reload_from_disk`).
- [x] Frequent commits — each task ends with a commit step; no task batches more than ~150 lines of change.
- [x] TDD pattern preserved: failing test → run → impl → run → commit.
