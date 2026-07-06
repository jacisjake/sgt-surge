# Schwab Migration & ORB Strategy — Design

**Date:** 2026-05-08
**Branch:** `cleaning`
**Status:** Approved, ready for implementation planning

## Goal

Replace the tastytrade broker integration with Charles Schwab API, and replace the
momentum-surge / momentum-pullback strategies with Opening Range Breakout (ORB) at
the same time. Single big-bang rewrite on the `cleaning` branch — no parallel
codepath, no compatibility shims.

## Context

- Repo is named `sgt-schwab` but the on-disk code is still tastytrade. The
  migration has not started; this spec is the starting point.
- Project memory: `sgt-schwab = Schwab + ORB`; the surge + tastytrade
  implementation lives in a sibling project.
- Deployment target: `ut.gitsum.rest` root path, `/opt/sgt-schwab/` on the server,
  Caddy reverse proxy, Podman container.
- Starting capital ~$270, goal $25,000.

## Decisions

### Strategy (ORB)

| Field            | Value                                                             |
| ---------------- | ----------------------------------------------------------------- |
| Symbol universe  | Pre-market gappers screener (top 5 via TradingView, configurable) |
| OR window        | 15 min (9:30–9:45 ET)                                             |
| Direction        | Long only (break of OR high)                                      |
| Entry rule       | Single 5-min close above OR high, with bar volume > OR volume / 3 |
| Stop             | OR low                                                            |
| Target           | Entry + 2R, with progressive R-trailing (existing exit machinery) |
| Trade cap        | Cash-account-constrained (no fixed cap, naturally ~1–2/day)       |
| Sizing           | Hybrid: ~90% BP deploy, capped by `MAX_POSITION_RISK_PCT` (default 1%) |
| Time-of-day stop | No new entries after 15:15 ET; safety-net close at 15:55 ET       |

### Broker

- **Library:** `schwab-py` (Alex Golec). De-facto standard, handles OAuth + auto
  refresh + REST + streaming.
- **Streaming:** Schwab WebSocket via `StreamClient`, day one. 1-min bars
  aggregated to 5-min internally.
- **Trading mode:** `TRADING_MODE=dry_run` is the default. The order executor
  intercepts orders and fabricates fills (entry and exit) at the current quote.
  Going live requires explicit `TRADING_MODE=live`.

### Deployment

- App path: `/opt/sgt-schwab/` on `ut.gitsum.rest`.
- OAuth callback: `https://ut.gitsum.rest/schwab/oauth/callback` (path-scoped, no
  DNS or Caddy work; no wildcard exists on `*.ut.gitsum.rest`).
- Token persistence: `/opt/sgt-schwab/state/schwab_token.json` on a Podman volume
  mount so OAuth survives image rebuilds.

## Architecture & Module Map

### Stays unchanged (broker-independent)

- `src/bot/scheduler.py` (only `NYSE_HOLIDAYS` import moves — see below)
- `src/bot/screener.py`, `src/bot/tradingview_screener.py`,
  `src/bot/float_provider.py` — scanner pipeline
- `src/bot/state/persistence.py`, `src/bot/state/trade_ledger.py`
- `src/risk/portfolio_limits.py`, `src/risk/position_sizer.py`
- `src/core/position_manager.py`
- `src/bot/web.py` — kept structurally; OAuth routes and a few endpoint payloads
  change

### Replaced (rewritten under new names)

| Old                             | New                          | Notes                                                       |
| ------------------------------- | ---------------------------- | ----------------------------------------------------------- |
| `src/core/tastytrade_client.py` | `src/core/schwab_client.py`  | REST wrapper around `schwab-py`                             |
| `src/core/tastytrade_ws.py`     | `src/core/schwab_stream.py`  | StreamClient wrapper, same callback shape                   |
| `src/core/order_executor.py`    | (same path, rewritten)       | Schwab order builders + dry-run intercept                   |
| `src/bot/main.py`               | (same path, edited)          | Imports updated, init swaps, logic preserved                |
| `src/bot/monitor.py`            | (same path, edited)          | Import swap and field-name fixes                            |
| `src/bot/stream_handler.py`     | (same path, edited)          | Import swap; callbacks already shape-compatible             |

### Deleted entirely

- `src/bot/signals/momentum_pullback.py`
- `src/bot/signals/momentum_surge.py`
- `src/core/regime_detector.py` (surge market gate; ORB doesn't need it)
- `src/bot/press_release_scanner.py` (catalyst-news enrichment; ORB is technical)

### New files

- `src/bot/signals/orb.py` — Opening Range Breakout signal generator
- `src/core/market_calendar.py` — extracted holiday/session calendar (was a
  `NYSE_HOLIDAYS` constant inside `tastytrade_client.py`)
- `tests/unit/test_orb_strategy.py`
- `tests/unit/test_schwab_client.py`
- `tests/unit/test_dry_run_executor.py`
- `tests/unit/test_bar_aggregator.py`
- `scripts/smoke_schwab.py` — integration smoke test (one-shot)

Net effect: ~9 files deleted, ~5 rewritten, ~7 new (4 tests + 1 script + 2
modules).

## Schwab Integration Layer

### `src/core/schwab_client.py`

REST wrapper around `schwab-py`. Three concerns:

1. **Token lifecycle.** On boot, try `easy_client(token_path, api_key,
   app_secret, callback_url)`. If the token file is missing or revoked,
   `is_authenticated` is `False`; `web.py`'s OAuth route handles a fresh
   `client_from_login_flow`-style flow initiated from the dashboard.
   `token_write_func` persists refreshed tokens to disk; the library handles
   ~30-min access-token refresh internally. 7-day refresh-token expiry requires
   the operator to re-OAuth via the dashboard at most weekly.
2. **Account hash.** Fetched once at construction via `get_account_numbers()`,
   cached on the instance. If `SCHWAB_ACCOUNT_HASH` is set in `.env`, that value
   is used directly; otherwise the first hash returned is used.
3. **Public surface** (named to mirror the previous tastytrade client so call
   sites in `main.py` / `monitor.py` / `stream_handler.py` change minimally):
   `is_authenticated`, `get_account()`, `get_buying_power()`, `get_equity()`,
   `get_positions()`, `get_position(symbol)`, `has_position(symbol)`,
   `get_bars(symbol, timeframe, limit)`, `get_latest_price(symbol)`,
   `get_latest_quotes(symbols)`, `submit_market_order`, `submit_limit_order`,
   `submit_stop_limit_order`, `cancel_order`, `cancel_all_orders`,
   `get_orders`, `get_asset(symbol)`, `is_fractionable(symbol)`.

News methods (`get_news`, etc.) are removed — Schwab has no news endpoint, and
the catalyst dependency is gone.

Schwab-specific quirks the wrapper hides:
- Account-hash routing on every order/position call.
- Schwab order-builder DSL (`equity_buy_market`, `equity_sell_to_close_market`,
  `equity_buy_limit`, etc. from `schwab.orders.equities`).
- `pricehistory` enum mapping for timeframes (e.g., `5Min` →
  `PriceHistory.PeriodType.DAY` + `FrequencyType.MINUTE` +
  `Frequency.EVERY_FIVE_MINUTES`).

### `src/core/schwab_stream.py`

Async wrapper around `schwab-py`'s `StreamClient`. Mirrors the existing
`TastytradeWSClient` callback shape so `stream_handler.py` is mostly unchanged.

Surface: `on_bar(callback)`, `on_quote(callback)`, `on_trade_update(callback)`,
`connect_data()`, `connect_trades()`, `subscribe(bars=[], quotes=[])`,
`unsubscribe`, `update_subscriptions`, `run_data_loop()`, `run_trade_loop()`,
plus status getters (`data_connected`, `trade_connected`, `subscribed_symbols`,
`get_status`).

Two adapters:
- **Bar aggregation.** Schwab's `chart_equity_subs` emits 1-min OHLCV. An
  internal `_BarAggregator` rolls 1-min into 5-min and emits via `on_bar`.
- **Trade updates.** Schwab streams `ACCT_ACTIVITY` for fills/cancels. The
  adapter normalizes those into the same `on_trade_update` payload shape the
  executor expects.

For OR-window construction itself, the strategy module fetches 9:30–9:45 via
REST `pricehistory` at 9:45:30 ET — not the stream — to avoid race conditions
with stream lag. Streaming kicks in for breakout detection from 9:45 onward.

### `src/core/order_executor.py`

Same public methods (`execute_market_order`, `execute_limit_order`,
`execute_stop_limit_order`, `cancel_*`, `get_open_orders`,
`get_order_status`). Internals swap:

- `_submit_order` builds a Schwab order via the appropriate
  `schwab.orders.equities` builder, then calls
  `client.place_order(account_hash, builder)`.
- `_wait_for_fill` polls `get_orders` (fill-status enum) until terminal state.
  Existing retry/backoff machinery preserved.
- **Dry-run intercept** at the top of `_submit_order`: if `config.trading_mode
  == "dry_run"`, log the would-be order, fabricate a synthetic `OrderResult`
  with `filled_qty = qty` and `filled_price = current_quote`, return
  immediately. Position manager records the position; monitor watches it; exit
  logic fires the same way. **Exits are also fabricated** at current quote, so
  the simulated round-trip closes cleanly.
- The dry-run flag is recorded on the position so the trade ledger can be
  filtered later.

## ORB Strategy Module

### `src/bot/signals/orb.py`

Single new file, replaces both deleted signal modules.

**Per-symbol state** (held in the strategy instance, keyed by symbol):
- `or_high`, `or_low`, `or_volume` — populated at 9:45:30 ET via REST
  `pricehistory(9:30→9:45)` aggregation
- `or_locked: bool` — true once the range is committed for the day
- `breakout_fired: bool` — true once a long signal has been emitted (prevents
  duplicate entries on the same OR)

**Daily lifecycle** (driven by scheduler hooks already present in the bot):

1. **9:25 ET — pre-market gappers screener fires** (existing TradingView
   screener path). Top `scanner_top_n` symbols (default 5) become the day's
   watchlist. ORB strategy registers each one with empty state.
2. **9:30 ET — market open.** Stream subscriptions go live for the watchlist
   (1-min bars + quotes). Strategy ignores all bars until OR is locked. Default
   watchlist size is `scanner_top_n = 5`; tunable in `.env`.
3. **9:45:30 ET — OR lock.** Scheduled job calls `strategy.lock_or(symbol)` for
   each watchlist symbol. The method does a REST `pricehistory` pull for the
   9:30–9:45 window, takes high/low/sum-volume, stores them, sets `or_locked =
   True`.
4. **9:45 → 15:15 ET — breakout watch.** On each `on_bar(symbol, bar)` callback
   (5-min aggregated; first eligible bar closes at 9:50 ET), if
   `or_locked and not breakout_fired`:
   - Entry rule: `bar.close > or_high` AND `bar.volume > or_volume / 3`
     (i.e., breakout bar volume ≥ the 5-min mean of the 15-min OR window)
   - On entry: emit `Signal(direction=LONG, entry_price=bar.close,
     stop_price=or_low, target_price=entry + 2*(entry - or_low))`. Set
     `breakout_fired = True`.
5. **15:15 ET — entry cutoff.** Strategy refuses any new signals after this
   point.
6. **15:55 ET — safety net.** Scheduler closes any open positions regardless of
   strategy state (existing EOD cleanup, untouched).
7. **Daily reset (06:00 ET).** Strategy state cleared per symbol.

### Exit logic — unchanged

Progressive R-trailing is already implemented in `monitor.py` +
`position_manager.py`:

- At entry: `stop_loss = or_low`, `initial_stop_loss = or_low`, `take_profit =
  entry + 2R`
- At unrealized +1R: stop ratchets to breakeven (entry price floor)
- Above +1R: chandelier overlay (`highest_price − chandelier_multiplier × ATR`)
  ratchets the stop monotonically upward; never lowered
- Take-profit hit at +2R closes the position
- Hard close at 15:55 ET regardless

The chandelier overlay needs `chandelier_atr` (seeded on bar-close in
`monitor._check_with_strategy`) and `chandelier_multiplier` (BotConfig). Both
stay; ORB benefits from the same upside-trailing behavior surge used.

The strategy module emits a single `Signal` per symbol per day; the existing
executor + monitor handle everything afterward.

### `Signal` dataclass cleanup

`src/bot/signals/base.py` is slimmed to what ORB actually emits:

```
Signal(symbol, direction, entry_price, stop_price, target_price, metadata)
```

Removed fields: `strength`, `risk_reward_ratio`, `has_catalyst`,
`news_headline`, `news_count`, `news_source`.

`src/bot/processor.py` drops:
- `min_signal_strength` check
- catalyst-strength bonus path
- regime-gate call (regime detector is being deleted)

Keeps: portfolio limits, position sizer, daytrade-count check, account-equity
sanity.

`BotConfig` deletes: `min_signal_strength`, `enable_regime_gate`, `regime_*`,
`pullback_*`, `enable_press_release_scanner`, `press_release_*`,
`scanner_enable_news_check`, `scanner_news_*`.

`chandelier_multiplier` and `atr_period` are retained — both are used by the
upside-trailing logic in `monitor.py` and are strategy-agnostic.

## Configuration, Web, OAuth

### `.env` schema

Replacement of the broker section in `.env.example`:

```
# ─── Schwab Authentication ────────────────────────────────────────────────────
SCHWAB_APP_KEY=your_schwab_app_key
SCHWAB_APP_SECRET=your_schwab_app_secret
SCHWAB_OAUTH_REDIRECT_URI=https://ut.gitsum.rest/schwab/oauth/callback
SCHWAB_TOKEN_PATH=state/schwab_token.json
# Optional: pin to a specific account if multiple are linked. If unset,
# the first hash returned by get_account_numbers() is used.
SCHWAB_ACCOUNT_HASH=

# ─── Trading Mode ─────────────────────────────────────────────────────────────
# 'dry_run' = simulated fills, no orders sent. 'live' = real orders.
TRADING_MODE=dry_run
```

All `TT_*` keys deleted. `FMP_API_KEY` deleted. Risk-management keys and
`LOG_LEVEL` retained.

### `BotConfig` net delta

Additions: `schwab_app_key`, `schwab_app_secret`, `schwab_oauth_redirect_uri`,
`schwab_token_path`, `schwab_account_hash` (Optional[str]). `trading_mode` enum
becomes `{dry_run, live}`; the previous `paper` value is removed (Schwab has no
paper-trading API — `dry_run` replaces it).

Deletions per the surge cleanup above.

### `src/bot/web.py` route changes

1. **`GET /schwab/oauth/start`** — new. Redirects the browser to Schwab's
   authorize URL (hand-built with `client_id`, `redirect_uri`).
2. **`GET /schwab/oauth/callback`** — replaces existing `/oauth/callback`.
   Receives `?code=...&session=...`, builds the full callback URL, calls
   `schwab.auth.client_from_received_url(api_key, app_secret, full_url,
   token_path)`, persists token, signals the running `SchwabClient` to reload
   from disk so auth picks up without restart, redirects to `/`.
3. **`GET /api/auth/status`** — returns Schwab-flavored fields:
   `{authenticated, account_hash, token_expires_at, token_age_seconds}`.

Other endpoints keep their JSON shapes; data sources change but the dashboard
HTML's contracts don't.

### Dashboard HTML

Single edit pass on the inline `DASHBOARD_HTML` in `web.py`:
- Remove surge-specific UI: catalyst news ribbons, regime indicator,
  press-release watchlist.
- Rename "Tastytrade" → "Schwab" in chrome.
- Replace strategy panel with ORB state per symbol: OR high/low, locked flag,
  breakout fired flag, current price vs OR high.

### Deploy script (`deploy/deploy-remote.sh`)

- Path: `/opt/sgt-surge/` → `/opt/sgt-schwab/`.
- Container/image names: `sgt-surge*` → `sgt-schwab*`.
- Token persistence: ensure `/opt/sgt-schwab/state/schwab_token.json` is on a
  Podman volume mount so OAuth survives image rebuilds.

### Caddy

No changes. `ut.gitsum.rest` already terminates TLS and reverse-proxies to the
bot on port 8080. The new `/schwab/oauth/*` paths flow through the same proxy
block.

### Caveat: redirect URI exact match

Schwab's portal validates the redirect URI byte-for-byte at token exchange.
Once `https://ut.gitsum.rest/schwab/oauth/callback` is registered, that exact
string must be in `.env` (`SCHWAB_OAUTH_REDIRECT_URI`) and emitted unchanged by
the bot's authorize-URL builder. Trailing slashes and case are load-bearing.

## Testing & Cutover

### Unit tests (mock Schwab)

- `tests/unit/test_orb_strategy.py` — synthetic 1-min bar fixtures covering:
  clean breakout, dead breakout (low volume), no breakout (range-bound),
  breakout below OR low (no signal — long-only), late-day breakout after 15:15
  cutoff, double-fire prevention.
- `tests/unit/test_schwab_client.py` — `respx`-mocked HTTP responses for
  `get_account_numbers`, `get_positions`, `pricehistory`, `place_order`.
  Verifies account-hash routing, timeframe→enum mapping, error envelope
  unwrapping.
- `tests/unit/test_dry_run_executor.py` — entry and exit fills are fabricated,
  no `place_order` is called when `trading_mode == "dry_run"`, synthetic fills
  propagate through to position manager and trade ledger.
- `tests/unit/test_bar_aggregator.py` — 1-min bars roll into 5-min correctly
  across the 9:30→9:45 boundary; partial bars don't fire early.

### Integration smoke test (`scripts/smoke_schwab.py`)

1. Boot bot in `dry_run` mode against live Schwab.
2. Verify auth (`is_authenticated == True`, account hash fetched).
3. Pull `pricehistory` for SPY, assert non-empty 5-min bars.
4. Open + close a streaming subscription for one symbol; assert at least one
   bar callback fires inside 90s during market hours.
5. Force a synthetic ORB signal at the current price; assert dry-run executor
   returns a fabricated fill, position manager has the position, monitor enters
   its watch loop.
6. Trigger `execute_exit`; assert dry-run exit fill, ledger records both legs,
   P&L computed.

This script is the gate for moving from `dry_run` to `live`.

### Cutover sequence

1. Land all code on the `cleaning` branch. CI green (unit tests pass).
2. Register the Schwab app, set callback to
   `https://ut.gitsum.rest/schwab/oauth/callback`. Get `app_key` + `app_secret`.
3. Deploy with `TRADING_MODE=dry_run`. Bot boots in setup mode (no token yet).
4. Visit dashboard, click OAuth start, complete flow in browser. Token persists
   to `/opt/sgt-schwab/state/schwab_token.json`. Bot reloads auth automatically;
   status flips to authenticated.
5. **Run dry-run for a full trading day.** Watch logs for: gappers screener
   output at 9:25, OR lock at 9:45, any breakout signals, any dry-run fills,
   any monitor exits, EOD safety net. Verify the trade ledger matches what your
   eye says should have happened.
6. Run dry-run for **at least 3 trading days** before flipping to live. Look
   for: scheduler not firing on time, missing OR data (Schwab pricehistory rate
   limits), bar-aggregator gaps, any unhandled exceptions in `_resilient_*_loop`
   restarts.
7. When confident: edit `.env` to `TRADING_MODE=live`, `podman restart
   sgt-schwab`. First live day starts with $270.

### Rollback

None formal. The previous tastytrade code is in git history if you ever need it
(`git log -- src/core/tastytrade_client.py`), but the migration deletes it from
`main`. There is no parallel codepath — the strangler approach was rejected for
that reason. If Schwab is broken, stop the container and don't trade. The
dry-run gate is the only safety mechanism.

### Branch strategy

All work continues on `cleaning`. When the smoke test passes and a dry-run day
looks clean, squash-merge to `main`. Don't merge until then — `main` should
not contain a half-migrated state.
