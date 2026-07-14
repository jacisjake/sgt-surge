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


_DASHBOARD_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>sgt-schwab — breakout_52w (live)</title>
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
    tr.clickable { cursor: pointer; }
    tr.clickable:hover td { background: #1c2230; }
    .spark { display: block; }
    /* modal */
    .overlay { position: fixed; inset: 0; background: rgba(1,4,9,0.75); display: none;
               align-items: center; justify-content: center; z-index: 50; }
    .overlay.open { display: flex; }
    .modal { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
             padding: 16px; width: 640px; max-width: calc(100vw - 32px); }
    .modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
    .modal-title { font-size: 15px; }
    .modal-sub { color: #8b949e; font-size: 12px; margin-left: 10px; }
    .toggle { display: inline-flex; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
    .toggle button { background: #21262d; color: #c9d1d9; border: none; padding: 5px 12px;
                     border-radius: 0; font-size: 12px; }
    .toggle button.active { background: #1f6feb; color: white; }
    .close-x { background: none; color: #8b949e; padding: 4px 8px; font-size: 16px; }
  </style>
</head>
<body>
  <h1>sgt-schwab — breakout_52w <span class="ok">(live)</span> · ORB <span style="color:#6e7681">(idle)</span></h1>

  <div class="panel">
    <strong>Auth:</strong> <span id="auth"></span>
    <span style="margin-left: 16px;"><strong>Mode:</strong> <span id="mode"></span></span>
    <span style="margin-left: 16px;"><strong>Token:</strong> <span id="token">—</span></span>
    <button id="oauth-btn" style="margin-left: 16px; display:none">Authorize Schwab</button>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Account</h2>
    <div id="account"></div>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">ORB state <span style="font-weight:normal;color:#6e7681">(idle — retired in favor of breakout_52w)</span></h2>
    <table id="orb-table"><thead><tr>
      <th>Symbol</th><th>OR High</th><th>OR Low</th><th>OR Vol</th><th>Locked</th><th>Fired</th><th>Price</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Positions</h2>
    <table id="pos-table"><thead><tr>
      <th>Symbol</th><th>Qty</th><th>Entry</th><th>Now</th><th>P&amp;L</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Open orders
      <span style="font-weight:normal;color:#6e7681">&mdash; live breakout_52w (fractional). Market orders queue as pending until the open.</span></h2>
    <table id="orders-table"><thead><tr>
      <th>Symbol</th><th>Qty</th><th>Type</th><th>Status</th><th>Filled</th><th>Submitted</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Strategy comparison
      <span style="font-weight:normal;color:#6e7681">&mdash; edge is sizing-normalized (per-trade price return)</span></h2>
    <table id="compare-table"><thead><tr>
      <th>Metric</th><th>ORB (live)</th><th>breakout_52w (paper)</th>
    </tr></thead><tbody></tbody></table>
    <div id="compare-real" style="margin-top:8px;font-size:12px;color:#8b949e"></div>
  </div>

  <div class="panel">
    <h2 style="font-size:14px;margin:0 0 8px;color:#8b949e">Paper Forward &mdash; breakout_52w
      <span id="paper-meta" style="font-weight:normal;color:#8b949e"></span></h2>
    <div id="paper-summary" style="margin-bottom:10px;font-size:13px"></div>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div style="flex:1;min-width:280px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">Open positions</div>
        <table id="paper-open"><thead><tr>
          <th>Symbol</th><th>Entry date</th><th>Entry</th><th>Stop</th><th>Notional</th>
        </tr></thead><tbody></tbody></table>
      </div>
      <div style="flex:1;min-width:280px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">Recent closed</div>
        <table id="paper-closed"><thead><tr>
          <th>Symbol</th><th>Held</th><th>P&amp;L</th><th>Reason</th>
        </tr></thead><tbody></tbody></table>
      </div>
    </div>
  </div>

  <div id="overlay" class="overlay">
    <div class="modal">
      <div class="modal-head">
        <div>
          <span id="m-title" class="modal-title"></span>
          <span id="m-sub" class="modal-sub"></span>
        </div>
        <div>
          <span class="toggle">
            <button id="t-line" class="active">Line</button>
            <button id="t-candles">Candles</button>
          </span>
          <button id="m-close" class="close-x">&times;</button>
        </div>
      </div>
      <div id="m-chart"></div>
    </div>
  </div>

<script>
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(name, attrs) {
  const el = document.createElementNS(SVGNS, name);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}
function setText(id, value, cls) {
  const el = document.getElementById(id);
  el.textContent = value;
  if (cls !== undefined) el.className = cls;
}

function makeCell(c) {
  const td = document.createElement('td');
  if (c.node) td.appendChild(c.node);
  else td.textContent = c.text;
  if (c.cls) td.className = c.cls;
  return td;
}

function renderTable(tbodySelector, rows) {
  const tbody = document.querySelector(tbodySelector);
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  for (const row of rows) {
    const cells = row.cells || row;
    const tr = document.createElement('tr');
    for (const c of cells) tr.appendChild(makeCell(c));
    if (row.onClick) {
      tr.className = 'clickable';
      tr.addEventListener('click', row.onClick);
    }
    tbody.appendChild(tr);
  }
}

function sparkline(closes, orHigh, orLow) {
  const w = 120, h = 28;
  const svg = svgEl('svg', {width: w, height: h, class: 'spark', viewBox: '0 0 ' + w + ' ' + h});
  if (!closes || closes.length < 2) {
    const t = svgEl('text', {x: 2, y: h - 9, fill: '#8b949e', 'font-size': 11});
    t.textContent = '—';
    svg.appendChild(t);
    return svg;
  }
  let lo = Math.min.apply(null, closes), hi = Math.max.apply(null, closes);
  if (orHigh != null) hi = Math.max(hi, orHigh);
  if (orLow != null) lo = Math.min(lo, orLow);
  const pad = 3, span = (hi - lo) || 1;
  const x = i => pad + i * (w - 2 * pad) / (closes.length - 1);
  const y = v => pad + (hi - v) * (h - 2 * pad) / span;
  if (orHigh != null) svg.appendChild(svgEl('line', {x1: 0, x2: w, y1: y(orHigh), y2: y(orHigh),
      stroke: '#d29922', 'stroke-width': 0.5, 'stroke-dasharray': '2 2', opacity: 0.6}));
  if (orLow != null) svg.appendChild(svgEl('line', {x1: 0, x2: w, y1: y(orLow), y2: y(orLow),
      stroke: '#8b949e', 'stroke-width': 0.5, 'stroke-dasharray': '2 2', opacity: 0.4}));
  const up = closes[closes.length - 1] >= closes[0];
  svg.appendChild(svgEl('polyline', {points: closes.map((c, i) => x(i) + ',' + y(c)).join(' '),
      fill: 'none', stroke: up ? '#3fb950' : '#f85149', 'stroke-width': 1.2}));
  return svg;
}

// ── Detail modal ──────────────────────────────────────────────────────
let mSymbol = null, mData = null, mTimer = null;
let mMode = localStorage.getItem('chartMode') || 'line';

function chartScales(d, W, H) {
  const bars = d.bars || [];
  const m = {l: 48, r: 12, t: 12, b: 18};
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); }
  if (d.or_high != null) hi = Math.max(hi, d.or_high);
  if (d.or_low != null) lo = Math.min(lo, d.or_low);
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  const span = (hi - lo) || 1, n = bars.length;
  return {bars, m, iw, ih, lo, hi, n,
    x: i => m.l + (n <= 1 ? iw / 2 : i * iw / (n - 1)),
    y: v => m.t + (hi - v) * ih / span};
}

function axisAndOR(svg, s, d) {
  [s.lo, (s.lo + s.hi) / 2, s.hi].forEach(function (v) {
    svg.appendChild(svgEl('line', {x1: s.m.l, x2: s.m.l + s.iw, y1: s.y(v), y2: s.y(v),
        stroke: '#21262d', 'stroke-width': 0.5}));
    const t = svgEl('text', {x: 4, y: s.y(v) + 3, fill: '#8b949e', 'font-size': 10});
    t.textContent = '$' + v.toFixed(2);
    svg.appendChild(t);
  });
  function orLine(v, color, label, dy) {
    svg.appendChild(svgEl('line', {x1: s.m.l, x2: s.m.l + s.iw, y1: s.y(v), y2: s.y(v),
        stroke: color, 'stroke-width': 1, 'stroke-dasharray': '4 3'}));
    const t = svgEl('text', {x: s.m.l + s.iw - 2, y: s.y(v) + dy, fill: color,
        'font-size': 10, 'text-anchor': 'end'});
    t.textContent = label;
    svg.appendChild(t);
  }
  if (d.or_high != null) orLine(d.or_high, '#d29922', 'OR high', -3);
  if (d.or_low != null) orLine(d.or_low, '#8b949e', 'OR low', 11);
}

function lineChart(d) {
  const W = 600, H = 300;
  const svg = svgEl('svg', {width: W, height: H, viewBox: '0 0 ' + W + ' ' + H});
  const s = chartScales(d, W, H);
  axisAndOR(svg, s, d);
  if (s.n >= 2) {
    const up = s.bars[s.n - 1].c >= s.bars[0].c;
    svg.appendChild(svgEl('polyline', {points: s.bars.map((b, i) => s.x(i) + ',' + s.y(b.c)).join(' '),
        fill: 'none', stroke: up ? '#3fb950' : '#f85149', 'stroke-width': 1.5}));
    const last = s.bars[s.n - 1];
    svg.appendChild(svgEl('circle', {cx: s.x(s.n - 1), cy: s.y(last.c), r: 3, fill: up ? '#3fb950' : '#f85149'}));
  } else {
    const t = svgEl('text', {x: W / 2, y: H / 2, fill: '#8b949e', 'font-size': 12, 'text-anchor': 'middle'});
    t.textContent = 'no bars buffered yet';
    svg.appendChild(t);
  }
  return svg;
}

function candleChart(d) {
  const W = 600, H = 300, volH = 46;
  const svg = svgEl('svg', {width: W, height: H, viewBox: '0 0 ' + W + ' ' + H});
  const s = chartScales(d, W, H - volH);
  axisAndOR(svg, s, d);
  const n = s.n, bw = Math.max(2, (s.iw / Math.max(n, 1)) * 0.6);
  const xc = i => s.m.l + (i + 0.5) * s.iw / Math.max(n, 1);
  let maxV = 0;
  for (const b of s.bars) maxV = Math.max(maxV, b.v || 0);
  maxV = maxV || 1;
  const vy = v => H - (v / maxV) * (volH - 4);
  for (let i = 0; i < n; i++) {
    const b = s.bars[i], up = b.c >= b.o, col = up ? '#3fb950' : '#f85149';
    svg.appendChild(svgEl('line', {x1: xc(i), x2: xc(i), y1: s.y(b.h), y2: s.y(b.l), stroke: col, 'stroke-width': 1}));
    const top = Math.min(s.y(b.o), s.y(b.c)), hgt = Math.max(1, Math.abs(s.y(b.c) - s.y(b.o)));
    svg.appendChild(svgEl('rect', {x: xc(i) - bw / 2, y: top, width: bw, height: hgt, fill: col}));
    svg.appendChild(svgEl('rect', {x: xc(i) - bw / 2, y: vy(b.v || 0), width: bw, height: H - vy(b.v || 0),
        fill: col, opacity: 0.5}));
  }
  svg.appendChild(svgEl('line', {x1: s.m.l, x2: s.m.l + s.iw, y1: H - volH, y2: H - volH,
      stroke: '#30363d', 'stroke-width': 0.5}));
  if (n === 0) {
    const t = svgEl('text', {x: W / 2, y: H / 2, fill: '#8b949e', 'font-size': 12, 'text-anchor': 'middle'});
    t.textContent = 'no bars buffered yet';
    svg.appendChild(t);
  }
  return svg;
}

function drawDetail() {
  if (!mData) return;
  const host = document.getElementById('m-chart');
  while (host.firstChild) host.removeChild(host.firstChild);
  host.appendChild(mMode === 'candles' ? candleChart(mData) : lineChart(mData));
}

function setMode(mode) {
  mMode = mode;
  localStorage.setItem('chartMode', mode);
  document.getElementById('t-line').className = mode === 'line' ? 'active' : '';
  document.getElementById('t-candles').className = mode === 'candles' ? 'active' : '';
  drawDetail();
}

async function loadDetail() {
  if (!mSymbol) return;
  mData = await (await fetch('/sgt/api/bars?symbol=' + encodeURIComponent(mSymbol))).json();
  document.getElementById('m-title').textContent = mSymbol;
  const band = (mData.or_low != null ? '$' + mData.or_low.toFixed(2) : '-')
             + ' – ' + (mData.or_high != null ? '$' + mData.or_high.toFixed(2) : '-');
  document.getElementById('m-sub').textContent = 'OR ' + band
    + (mData.current != null ? '  ·  cur $' + mData.current.toFixed(2) : '')
    + (mData.fired ? '  ·  BREAKOUT' : '');
  drawDetail();
}

async function openModal(sym) {
  mSymbol = sym;
  document.getElementById('overlay').classList.add('open');
  setMode(mMode);
  await loadDetail();
  if (mTimer) clearInterval(mTimer);
  mTimer = setInterval(loadDetail, 2000);
}

function closeModal() {
  document.getElementById('overlay').classList.remove('open');
  mSymbol = null;
  if (mTimer) { clearInterval(mTimer); mTimer = null; }
}

async function refresh() {
  const status = await (await fetch('/sgt/api/status')).json();
  // Drive the auth badge off the real broker state in /api/status, not
  // /api/auth/status — the latter only reports that a client loaded at
  // startup and stays "authenticated" even when the refresh token is dead.
  const authOk = status.mode === 'running';
  const authLabel = status.mode === 'error' ? 'token error'
                  : status.mode === 'setup' ? 'unauthenticated'
                  : authOk ? 'authenticated' : (status.mode || 'unknown');
  setText('auth', authLabel, authOk ? 'ok' : 'err');
  document.getElementById('oauth-btn').style.display =
      authOk ? 'none' : 'inline-block';

  setText('mode', status.mode || '-');

  // Refresh-token expiry — the thing that used to die silently every 7 days.
  const tk = status.token;
  if (tk && tk.expires_at) {
    if (tk.expired) {
      setText('token', 'EXPIRED — re-auth', 'err');
    } else {
      const d = tk.days_remaining;
      setText('token', 'expires in ' + d.toFixed(1) + 'd', d <= 2 ? 'warn' : 'ok');
    }
  } else {
    setText('token', '—');
  }

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
  const bars = await (await fetch('/sgt/api/bars')).json();
  const orbRows = Object.entries(orb).map(function (entry) {
    const sym = entry[0]; const st = entry[1];
    const b = bars[sym] || {};
    return {
      cells: [
        {text: sym},
        {text: '$' + st.or_high.toFixed(2)},
        {text: '$' + st.or_low.toFixed(2)},
        {text: st.or_volume.toLocaleString()},
        {text: st.or_locked ? 'YES' : 'no', cls: st.or_locked ? 'ok' : 'warn'},
        {text: st.breakout_fired ? 'YES' : 'no', cls: st.breakout_fired ? 'ok' : ''},
        {node: sparkline(b.closes, st.or_high, st.or_low)},
      ],
      onClick: function () { openModal(sym); },
    };
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

  const orders = await (await fetch('/sgt/api/orders')).json();
  const ordRows = orders.map(function (o) {
    const s = (o.status || '').toLowerCase();
    const cls = s === 'filled' ? 'ok'
              : (s.indexOf('reject') >= 0 || s.indexOf('cancel') >= 0 || s.indexOf('expired') >= 0) ? 'err'
              : 'warn';
    return [
      {text: o.symbol},
      {text: String(o.qty)},
      {text: o.type || '-'},
      {text: o.status || '-', cls: cls},
      {text: String(o.filled_qty || 0)},
      {text: (o.submitted_at || '').replace('T', ' ').slice(0, 16)},
    ];
  });
  renderTable('#orders-table tbody',
    ordRows.length ? ordRows : [[{text: 'No open orders', cls: ''}, {text: ''}, {text: ''}, {text: ''}, {text: ''}, {text: ''}]]);

  const paper = await (await fetch('/sgt/api/paper')).json();
  if (paper.exists) {
    setText('paper-meta', '· as of ' + (paper.last_date || '-'));
    const ret = paper.total_return || 0;
    document.getElementById('paper-summary').innerHTML =
      'Equity <b>$' + paper.equity.toFixed(2) + '</b> &nbsp;|&nbsp; Return '
      + '<b class="' + (ret >= 0 ? 'ok' : 'err') + '">' + (ret * 100).toFixed(1) + '%</b>'
      + ' &nbsp;|&nbsp; Open ' + paper.n_open
      + ' &nbsp;|&nbsp; Closed ' + paper.n_closed
      + ' (win ' + (paper.win_rate * 100).toFixed(0) + '%)';
    renderTable('#paper-open tbody', paper.open_positions.map(function (p) {
      return [{text: p.symbol}, {text: p.entry_date},
              {text: '$' + (p.entry_price || 0).toFixed(2)},
              {text: '$' + (p.stop_price || 0).toFixed(2)},
              {text: '$' + (p.notional || 0).toFixed(2)}];
    }));
    renderTable('#paper-closed tbody', paper.closed_trades.map(function (t) {
      const pnl = t.pnl || 0;
      return [{text: t.symbol},
              {text: (t.entry_date || '') + ' → ' + (t.exit_date || '')},
              {text: '$' + pnl.toFixed(2), cls: pnl >= 0 ? 'ok' : 'err'},
              {text: t.reason || ''}];
    }));
  } else {
    document.getElementById('paper-summary').textContent =
      'No paper-test data yet (first run 16:30 ET).';
  }

  const cmp = await (await fetch('/sgt/api/compare')).json();
  if (cmp.orb && cmp.paper) {
    const pct = function (x) { return (x * 100).toFixed(1) + '%'; };
    const signed = function (x) { return {text: pct(x), cls: x >= 0 ? 'ok' : (x < 0 ? 'err' : '')}; };
    const o = cmp.orb, p = cmp.paper;
    renderTable('#compare-table tbody', [
      [{text: 'Closed trades'}, {text: String(o.n_closed)}, {text: String(p.n_closed)}],
      [{text: 'Win rate'}, {text: pct(o.win_rate)}, {text: pct(p.win_rate)}],
      [{text: 'Avg win'}, signed(o.avg_win), signed(p.avg_win)],
      [{text: 'Avg loss'}, signed(o.avg_loss), signed(p.avg_loss)],
      [{text: 'Expectancy / trade'}, signed(o.expectancy), signed(p.expectancy)],
      [{text: 'Cum. return (equal-weight)'}, signed(o.norm_return), signed(p.norm_return)],
    ]);
    const eq = (o.account_equity != null) ? ('$' + o.account_equity.toFixed(2)) : '—';
    document.getElementById('compare-real').innerHTML =
      'Real account (ORB): equity <b>' + eq + '</b> &nbsp;|&nbsp; realized '
      + '<b class="' + (o.realized_pnl >= 0 ? 'ok' : 'err') + '">$' + o.realized_pnl.toFixed(2) + '</b>'
      + ' &nbsp;·&nbsp; paper realized <b class="' + (p.realized_pnl >= 0 ? 'ok' : 'err')
      + '">$' + p.realized_pnl.toFixed(2) + '</b> (sim)';
  }
}

document.getElementById('oauth-btn').addEventListener('click', function () {
  window.location = '/schwab/oauth/start';
});

document.getElementById('t-line').addEventListener('click', function () { setMode('line'); });
document.getElementById('t-candles').addEventListener('click', function () { setMode('candles'); });
document.getElementById('m-close').addEventListener('click', closeModal);
document.getElementById('overlay').addEventListener('click', function (e) {
  if (e.target.id === 'overlay') closeModal();
});
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });

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


@app.get("/api/status")
async def status() -> dict:
    if _bot is None or not _bot.client.is_authenticated:
        return {"mode": "setup", "authenticated": False, "token": _token_block()}
    try:
        account = _bot.client.get_account()
    except Exception as e:
        return {"mode": "error", "error": str(e), "token": _token_block()}
    return {
        "mode": "running",
        "authenticated": True,
        "account": account,
        "trading_mode": str(_bot.config.trading_mode.value),
        "token": _token_block(),
    }


@app.get("/api/orders")
async def open_orders() -> list:
    """Open/pending broker orders — surfaces live breakout_52w fractional orders
    that are queued (pending_activation) and not yet in Positions."""
    if _bot is None or not _bot.client.is_authenticated:
        return []
    try:
        return _bot.client.get_orders(status="open")
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


@app.get("/api/positions")
async def positions() -> list[dict]:
    if _bot is None:
        return []
    return [p.to_dict() for p in _bot.position_manager.get_open_positions()]


@app.get("/api/paper")
async def paper_forward() -> dict:
    """Read-only view of the breakout_52w paper-forward ledger written daily by
    scripts/research/swing/paper_forward.py. Simulated only — no real orders."""
    if _bot is None:
        return {"exists": False}
    try:
        path = Path(_bot.config.state_dir) / "swing_paper_breakout.json"
        if not path.exists():
            return {"exists": False}
        data = json.loads(path.read_text())
    except Exception:
        return {"exists": False}

    start = float(data.get("starting_equity", 0.0) or 0.0)
    realized = float(data.get("realized_pnl", 0.0) or 0.0)
    equity = start + realized
    closed = data.get("closed_trades", [])
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    return {
        "exists": True,
        "equity": equity,
        "starting_equity": start,
        "total_return": (equity / start - 1.0) if start else 0.0,
        "realized_pnl": realized,
        "n_open": len(data.get("open_positions", [])),
        "n_closed": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "last_date": data.get("last_date"),
        "open_positions": data.get("open_positions", []),
        "closed_trades": list(reversed(closed))[:25],
    }


@app.get("/api/compare")
async def compare() -> dict:
    """Head-to-head of live ORB vs the breakout_52w paper test.

    Edge metrics (win rate, avg win/loss, expectancy, equal-weight return) are
    sizing-independent — each closed trade is reduced to its price return
    exit/entry-1 — so the two can be compared despite different position sizing.
    ORB's real-dollar P&L and live account equity are reported separately.
    """
    if _bot is None:
        return {"orb": None, "paper": None}

    # Live ORB — from the persistent trade ledger.
    orb_trades = _bot.trade_ledger.get_trades(limit=10_000)
    orb = comparison_stats(trade_returns(orb_trades, "entry_price", "exit_price"))
    orb["realized_pnl"] = _bot.trade_ledger.get_total_realized_pnl()
    try:
        orb["account_equity"] = _bot.client.get_account().get("equity")
    except Exception:
        orb["account_equity"] = None

    # Paper breakout_52w — from the daily ledger.
    paper = comparison_stats([])
    paper["realized_pnl"] = 0.0
    try:
        path = Path(_bot.config.state_dir) / "swing_paper_breakout.json"
        if path.exists():
            data = json.loads(path.read_text())
            closed = data.get("closed_trades", [])
            paper = comparison_stats(trade_returns(closed, "entry_price", "exit_price"))
            paper["realized_pnl"] = float(data.get("realized_pnl", 0.0) or 0.0)
    except Exception:
        pass

    return {"orb": orb, "paper": paper}


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
