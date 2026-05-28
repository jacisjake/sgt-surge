"""
Bot dashboard + OAuth callback server.

FastAPI app. Schwab OAuth flow at /schwab/oauth/{start,callback}.
Dashboard HTML and /api/* endpoints are added in Task 20.
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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
  const auth = await (await fetch('/sgt/api/auth/status')).json();
  setText('auth', auth.authenticated ? 'authenticated' : 'unauthenticated',
          auth.authenticated ? 'ok' : 'err');
  document.getElementById('oauth-btn').style.display =
      auth.authenticated ? 'none' : 'inline-block';

  const status = await (await fetch('/sgt/api/status')).json();
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

  const orb = await (await fetch('/sgt/api/orb')).json();
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

  const positions = await (await fetch('/sgt/api/positions')).json();
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
        # Owner-only at creation time so the refresh token never lands on disk
        # world-readable, even briefly. /opt/sgt-schwab is on a shared host.
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(token, f)

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
