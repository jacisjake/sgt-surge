"""
Bot dashboard + OAuth callback server.

FastAPI app. Schwab OAuth flow at /schwab/oauth/{start,callback}.
Dashboard HTML and /api/* endpoints are added in Task 20.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.bot.comparison import comparison_stats, trade_returns
from src.core.schwab_token import read_token_status

try:
    from schwab.auth import client_from_received_url, get_auth_context
except ImportError:  # pragma: no cover
    client_from_received_url = None
    get_auth_context = None


app = FastAPI(title="sgt-schwab", version="1.0.0")

_bot = None

# AuthContexts produced by /schwab/oauth/start, keyed by their OAuth state.
# /schwab/oauth/callback looks them up by the `state` query param Schwab
# sends back. Module-level state is fine: a single bot process drives one
# OAuth flow at a time.
_pending_auth_contexts: dict = {}


def set_bot(bot) -> None:
    """Called by run_bot.py at startup to give the API access to the bot."""
    global _bot
    _bot = bot



@app.get("/")
async def dashboard():
    return HTMLResponse(Path(__file__).with_name("dashboard.html").read_text())


@app.get("/schwab/oauth/start")
async def schwab_oauth_start():
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    if get_auth_context is None:
        raise HTTPException(500, "schwab-py is not installed")
    ctx = get_auth_context(
        api_key=_bot.config.schwab_app_key,
        callback_url=_bot.config.schwab_oauth_redirect_uri,
    )
    _pending_auth_contexts[ctx.state] = ctx
    return RedirectResponse(ctx.authorization_url, status_code=307)


@app.get("/schwab/oauth/callback")
async def schwab_oauth_callback(request: Request):
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    state = request.query_params.get("state", "")
    ctx = _pending_auth_contexts.pop(state, None)
    if ctx is None:
        raise HTTPException(
            400,
            "OAuth state not found or already used. "
            "Restart the flow at /schwab/oauth/start.",
        )

    # Caddy proxies the bot at localhost:8080, so request.url is the internal
    # URL. Rebuild the public URL Schwab actually redirected to; schwab-py
    # validates state against this string.
    received_url = f"{_bot.config.schwab_oauth_redirect_uri}?{request.url.query}"

    token_path = _bot.config.schwab_token_path

    def token_write_func(token, *args, **kwargs):
        # On a shared host the refresh token is a real credential, so write
        # it via a 0o600 tempfile in the same dir and atomically rename into
        # place. tempfile.mkstemp creates with O_EXCL|O_CREAT|O_RDWR and mode
        # 0o600, so we never follow a symlink at token_path and never expose
        # the file with relaxed permissions even briefly.
        import tempfile
        dirname = os.path.dirname(token_path) or "."
        fd, tmp_path = tempfile.mkstemp(
            prefix=".schwab_token.", dir=dirname, text=True
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(token, f)
            os.replace(tmp_path, token_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    try:
        client_from_received_url(
            api_key=_bot.config.schwab_app_key,
            app_secret=_bot.config.schwab_app_secret,
            auth_context=ctx,
            received_url=received_url,
            token_write_func=token_write_func,
        )
    except Exception as e:
        raise HTTPException(400, f"OAuth exchange failed: {e}")
    _bot.client.reload_from_disk()
    return RedirectResponse("/sgt/", status_code=302)


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


@app.get("/api/auth/status")
async def auth_status() -> dict:
    if _bot is None:
        return {"authenticated": False, "account_hash": None, "broker": "schwab"}
    return {
        "authenticated": _bot.client.is_authenticated,
        "account_hash": _bot.client.account_hash,
        "broker": "schwab",
    }


def _token_block() -> dict:
    """Refresh-token expiry, so the dashboard can warn before the 7-day death."""
    if _bot is None:
        return {}
    try:
        st = read_token_status(_bot.config.schwab_token_path)
    except Exception:  # noqa: BLE001 — status must never 500 over this
        return {}
    return {
        "expires_at": st["expires_at"],
        "days_remaining": st["days_remaining"],
        "expired": st["expired"],
    }


def _cached_account():
    snap = getattr(_bot, "_account_snapshot", None)
    if isinstance(snap, dict):
        return snap
    account = _bot.client.get_account()
    _bot._account_snapshot = account
    return account


def _cached_orders():
    snap = getattr(_bot, "_open_orders_snapshot", None)
    if isinstance(snap, list):
        return snap
    orders = _bot.client.get_orders(status="open")
    if isinstance(orders, list):
        _bot._open_orders_snapshot = orders
    return orders


@app.get("/api/status")
async def status() -> dict:
    enable_orb = bool(getattr(_bot.config, "enable_orb_live", False)) if _bot else False
    trading_mode = (
        str(_bot.config.trading_mode.value)
        if _bot and getattr(_bot.config, "trading_mode", None) is not None
        else None
    )
    if _bot is None or not _bot.client.is_authenticated:
        return {
            "mode": "setup",
            "authenticated": False,
            "token": _token_block(),
            "trading_mode": trading_mode,
            "enable_orb_live": enable_orb,
        }
    try:
        account = _cached_account()
    except Exception as e:
        return {
            "mode": "error",
            "error": str(e),
            "token": _token_block(),
            "trading_mode": trading_mode,
            "enable_orb_live": enable_orb,
        }
    return {
        "mode": "running",
        "authenticated": True,
        "account": account,
        "trading_mode": trading_mode or str(_bot.config.trading_mode.value),
        "enable_orb_live": enable_orb,
        "token": _token_block(),
    }


@app.get("/api/orders")
async def open_orders() -> list:
    """Open/pending broker orders — surfaces live breakout_52w fractional orders
    that are queued (pending_activation) and not yet in Positions."""
    if _bot is None or not _bot.client.is_authenticated:
        return []
    try:
        orders = _cached_orders()
        return orders if isinstance(orders, list) else []
    except Exception:  # noqa: BLE001 — the dashboard must never 500 over this
        return []


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


@app.get("/api/bars")
async def bars(symbol: Optional[str] = None) -> dict:
    """Buffered 5-min bars for the dashboard charts.

    Without ?symbol, returns a lightweight per-symbol close series (keyed by
    the ORB watch list) for the sparkline column. With ?symbol, returns the
    full OHLCV series for that one symbol for the detail popup. OR-band and
    breakout flags are merged in from strategy state.
    """
    if _bot is None:
        return {}
    state = _bot.strategy.state

    if symbol is not None:
        st = state.get(symbol)
        bars = _bot.stream_handler.get_ohlcv(symbol)
        return {
            "symbol": symbol,
            "bars": bars,
            "or_high": st.or_high if st else None,
            "or_low": st.or_low if st else None,
            "fired": st.breakout_fired if st else False,
            "current": bars[-1]["c"] if bars else None,
        }

    series = _bot.stream_handler.get_close_series()
    out = {}
    for sym, st in state.items():
        closes = series.get(sym, [])
        out[sym] = {
            "closes": closes,
            "or_high": st.or_high,
            "or_low": st.or_low,
            "fired": st.breakout_fired,
            "current": closes[-1] if closes else None,
        }
    return out


@app.get("/api/education")
async def education_brief() -> dict:
    """Latest market-condition + playbook education card (read-only).

    Populated by ``python -m scripts.lab.market_brief``.
    Does not place orders.
    """
    state_dir = "state"
    if _bot is not None and getattr(_bot.config, "state_dir", None):
        state_dir = _bot.config.state_dir
    try:
        from src.lab.education.report import load_condition_report

        report = load_condition_report(state_dir=state_dir)
        if not report:
            return {"exists": False}
        return {"exists": True, **report}
    except Exception:  # noqa: BLE001
        return {"exists": False}


@app.get("/api/ops")
async def ops_snapshot() -> dict:
    """Last live_swing / flatten snapshots + live universe size."""
    state_dir = "state"
    if _bot is not None and getattr(_bot.config, "state_dir", None):
        state_dir = _bot.config.state_dir
    try:
        from src.lab.ops_snapshot import load_ops

        return load_ops(state_dir=state_dir)
    except Exception:  # noqa: BLE001
        return {"universe": {"n": 0, "exists": False}, "last_live_swing": None, "last_flatten": None}



@app.get("/api/positions")
async def positions() -> list[dict]:
    if _bot is None:
        return []
    return [p.to_dict() for p in _bot.position_manager.get_open_positions()]


@app.get("/api/compare")
async def compare() -> dict:
    """Edge metrics for the live ORB ledger.

    Win rate, avg win/loss, expectancy and equal-weight return are
    sizing-independent — each closed trade is reduced to the price return
    exit/entry-1. Live account equity is reported separately. ORB live money is
    retired by default (ENABLE_ORB_LIVE=false), so the ledger may be idle.
    """
    if _bot is None:
        return {"orb": None}

    orb_trades = _bot.trade_ledger.get_trades(limit=10_000)
    orb = comparison_stats(trade_returns(orb_trades, "entry_price", "exit_price"))
    orb["realized_pnl"] = _bot.trade_ledger.get_total_realized_pnl()
    try:
        orb["account_equity"] = _bot.client.get_account().get("equity")
    except Exception:
        orb["account_equity"] = None

    return {"orb": orb}


@app.post("/admin/lock_or_now")
async def admin_lock_or_now() -> dict:
    """Manually trigger the OR-lock job. Not routed via Caddy, so only
    reachable from the container host (curl http://localhost:8080/...).
    Used to recover from a missed 09:45:30 ET scheduler fire."""
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    await _bot._lock_opening_ranges()
    # _lock_opening_ranges saves state + history, but be explicit so a
    # manual admin lock leaves the same audit trail.
    _bot._save_orb_state()
    _bot._save_orb_history()
    return {
        "ok": True,
        "orb_state": {
            sym: {
                "or_high": st.or_high,
                "or_low": st.or_low,
                "or_volume": st.or_volume,
                "or_locked": st.or_locked,
            }
            for sym, st in _bot.strategy.state.items()
        },
    }


@app.post("/admin/sync_positions")
async def admin_sync_positions() -> dict:
    """Adopt broker-side positions the bot is unaware of.

    The OrderExecutor has a race where it polls Schwab's order status too
    fast after submission, gets back "failed", and abandons the fill --
    even when Schwab actually completed the order. The position lands on
    the account with no entry in PositionManager and no stop/target,
    which means the bot won't manage exits OR fire the EOD safety net.

    This endpoint reconciles by pulling broker positions, adding the
    missing ones to PositionManager using the ORB strategy's OR data for
    stop, and submitting a hard stop-limit order to Schwab for downside
    protection.

    Localhost-only via Caddy routing (same as /admin/lock_or_now).
    """
    from src.core.position_manager import PositionSide
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    broker_positions = _bot.client.get_positions()
    adopted = []
    for p in broker_positions:
        symbol = p["symbol"]
        qty = float(p["qty"])
        if qty <= 0:
            continue
        if _bot.position_manager.has_position(symbol):
            continue
        entry_price = float(p["avg_entry_price"])

        # Derive stop/target from ORB state if we have it; otherwise
        # fall back to a 5% stop so there's at least *some* protection.
        or_state = _bot.strategy.state.get(symbol)
        if or_state and or_state.or_locked and or_state.or_low > 0:
            stop = or_state.or_low
            r = max(entry_price - stop, 0.01)
            target = entry_price + 2 * r
        else:
            stop = round(entry_price * 0.95, 2)
            target = round(entry_price * 1.10, 2)

        position = _bot.position_manager.add_position(
            symbol=symbol,
            side=PositionSide.LONG,
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop,
            take_profit=target,
            strategy="orb",
        )

        # Submit a hard stop-limit at the broker so the position has real
        # downside protection independent of the bot process. Limit price
        # is 1% below stop trigger to favor execution over slippage on
        # illiquid penny names.
        broker_stop_id = None
        try:
            broker_stop_id = _bot.client.submit_stop_limit_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                stop_price=round(stop, 2),
                limit_price=round(stop * 0.99, 2),
            )
            position.broker_stop_order_id = broker_stop_id
        except Exception as e:
            logger.error(
                f"[SYNC] {symbol}: stop-limit submission failed: {e}. "
                f"Position adopted in PositionManager but no broker stop is active."
            )

        adopted.append({
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "stop_loss": stop,
            "take_profit": target,
            "broker_stop_order_id": broker_stop_id,
        })

    return {"adopted": adopted, "open_positions": len(_bot.position_manager.get_open_positions())}


@app.post("/admin/close_stale_positions")
async def admin_close_stale_positions() -> dict:
    """Reconcile PositionManager against the broker: anything we still
    think is open but Schwab no longer holds gets marked closed at the
    current quote with reason='reconciled (broker closed)'.

    Needed because the OrderExecutor races on order-status polling for
    EXITS too: TP hits, sell submitted, polled too fast, sees "failed",
    PositionManager never gets close_position called -- so the poll
    loop keeps re-detecting the TP and spamming sell retries to Schwab.
    """
    if _bot is None:
        raise HTTPException(503, "Bot not initialized")
    broker_positions = _bot.client.get_positions()
    broker_symbols = {
        p["symbol"] for p in broker_positions if float(p.get("qty", 0)) > 0
    }
    closed = []
    for position in list(_bot.position_manager.get_open_positions()):
        if position.symbol in broker_symbols:
            continue
        try:
            exit_price = _bot.client.get_latest_price(position.symbol)
        except Exception:
            exit_price = position.current_price or position.entry_price
        _bot.position_manager.close_position(
            position.symbol, exit_price, "reconciled (broker closed)"
        )
        closed.append({
            "symbol": position.symbol,
            "exit_price": exit_price,
        })
    return {
        "closed": closed,
        "open_positions_remaining": len(_bot.position_manager.get_open_positions()),
    }


@app.get("/api/scanner")
async def scanner() -> list[dict]:
    if _bot is None:
        return []
    return [
        {"symbol": c.symbol, "price": c.price, "change_pct": c.change_pct}
        for c in _bot._scanner_results[:20]
    ]
