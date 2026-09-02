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

- `/opt/sgt-schwab/` - Application files
- `/opt/sgt-schwab/.env` - Environment variables (Schwab API keys)
- Container volumes for state/logs

## Trading Context

- **Broker**: Charles Schwab API (schwab-py). Token auto-refreshes (`state/schwab_token.json`).
- **Starting capital**: ~$200 cash account; goal $25,000 (north-star measurement only).
- **Live ORB money**: **OFF by default** — `ENABLE_ORB_LIVE=false`; ops should use `TRADING_MODE=dry_run` on the server. Stream/scan may still run.
- **Primary edge under test**: `breakout_52w` (lab), backtest-gated before live.
- **Migration design**: see `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`
- **Trading Lab v1 design**: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`

## Strategy Switch — Current State (as of 2026-07-22)

- **ORB** — process kept for auth/stream/dashboard; real orders gated off. Idle since ~2026-06-04.
- **`breakout_52w`** — lab research experiment + live candidate after promote. Bake-off +55% research claim; the backtest report is the source of promotion truth.
- **`short_term_reversal`** — research stage in registry; promote after backtest report.
- **`runner_momentum`** — **spec only**; out of lab v1.

Decision gate: do NOT promote a strategy to live until its backtest report clears the soft gates (`--force` audit only when intentional).

Bake-off findings: `docs/superpowers/results/2026-06-11-strategy-bakeoff.md`.

## Trading Lab v1

Design: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`

- **Protocol / strategies:** `src/lab/protocol.py`, `src/lab/strategies/`
- **SimFill:** `src/lab/fills/sim.py`
- **Registry:** `config/experiments.yaml` + `state/experiments/overrides.yaml` (stage/promote)
- **CLI:**
  - `python -m scripts.lab.run_experiment --id breakout_52w_live`
  - `python -m scripts.lab.promote <id> --check|--to live|--demote research`
  - `python -m scripts.lab.scoreboard --id breakout_52w_live`
  - `python -m scripts.lab.journal_report --id breakout_52w_live` (skew/acceptance metrics)
  - `python -m scripts.lab.sweep_convex --fetch` (k1×k2 grid; selects on skew, never on total return)
  - `python -m scripts.lab.market_brief` / `--narrative` / `--json` (conditions + playbook)
- **Education:** `config/playbook.yaml` + sensors in `src/lab/education/`; agent prompt `config/prompts/market_education.md`; dashboard **Today's tape** via `/api/education`
- **Live:** LiveRunner preview default; real submits need stage=live + `TRADING_MODE=live` + `ENABLE_ORB_LIVE=false`
- **Ledger:** `state/experiments/<id>/ledger.json`; closed-trade journal `state/experiments/<id>/journal.json` (R-multiple, exit reason, regime at entry)
- **Cron:** token watch only; it alerts on stage=live ledger staleness (>3 weekdays)
- **Capital safety:** leave `ENABLE_ORB_LIVE` false; prefer server `TRADING_MODE=dry_run`
- **No paper stage:** removed 2026-09-02. One execution system; `dry_run` is the rehearsal mode.

## Lab cutover checklist (server)

1. Set `/opt/sgt-schwab/.env` → `TRADING_MODE=dry_run` (leave `ENABLE_ORB_LIVE` unset/false).
2. Restart **only** `sgt-schwab-bot` (never `podman stop -a`).
3. Verify `/api/status` shows `"trading_mode":"dry_run"`. `"mode":"running"` is auth-based, not ORB trading.
4. Keep token refresh cron; run `bash scripts/healthcheck.sh` for operator checks.
