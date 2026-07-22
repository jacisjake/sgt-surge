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
- **Primary edge under test**: `breakout_52w` paper experiment (lab), weekday cron via `run_paper_forward.sh`.
- **Migration design**: see `docs/superpowers/specs/2026-05-08-schwab-migration-design.md`
- **Trading Lab v1 design**: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`

## Strategy Switch — Current State (as of 2026-07-22)

- **ORB** — process kept for auth/stream/dashboard; real orders gated off. Idle since ~2026-06-04.
- **`breakout_52w`** — lab paper experiment + optional live candidate after promote. Bake-off +55% research claim; forward paper is source of promotion truth.
- **`short_term_reversal`** — research stage in registry; promote after backtest report.
- **`runner_momentum`** — **spec only**; out of lab v1.

Decision gate: do NOT promote a strategy to live until paper forward proves out (soft gates + `--force` audit only when intentional).

Bake-off findings: `docs/superpowers/results/2026-06-11-strategy-bakeoff.md`.

## Trading Lab v1

Design: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md`

- **Protocol / strategies:** `src/lab/protocol.py`, `src/lab/strategies/`
- **SimFill:** `src/lab/fills/sim.py`
- **Registry:** `config/experiments.yaml` + `state/experiments/overrides.yaml` (stage/promote)
- **CLI:**
  - `python -m scripts.lab.run_experiment --id breakout_52w_paper`
  - `python -m scripts.lab.promote --check|--to paper|live|--demote`
  - `python -m scripts.lab.scoreboard --id breakout_52w_paper`
- **Live:** LiveRunner preview default; real submits need stage=live + `TRADING_MODE=live` + `ENABLE_ORB_LIVE=false`
- **Paper ledger:** `state/experiments/breakout_52w_paper/ledger.json` (auto-migrates from `state/swing_paper_breakout.json`)
- **Cron:** `run_paper_forward.sh` → lab `run_experiment`; token watch also alerts on ledger staleness (>3 weekdays)
- **Capital safety:** leave `ENABLE_ORB_LIVE` false; prefer server `TRADING_MODE=dry_run`

## Lab cutover checklist (server)

1. Set `/opt/sgt-schwab/.env` → `TRADING_MODE=dry_run` (leave `ENABLE_ORB_LIVE` unset/false).
2. Restart **only** `sgt-schwab-bot` (never `podman stop -a`).
3. Verify `/api/status` shows `"trading_mode":"dry_run"`. `"mode":"running"` is auth-based, not ORB trading.
4. Keep token refresh + `run_paper_forward.sh` cron; run `bash scripts/healthcheck.sh` for operator checks.
5. Optional: one-time paper catch-up if ledger stalled — uses lab path via `paper_catchup.py`.
