# Trading Lab v1 — Safety Batch (PRs 1–4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Capital safety for idle ORB money path, remove deploy/script cruft, drop killed strategies with green tests, slim unused deps.

**Architecture:** No new lab runtime yet. Defaults + deletes only. Design source: `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md` §14 and PR Plan PR1–4.

**Tech Stack:** Python 3.11+, pydantic-settings, pytest, Podman deploy docs.

## Global Constraints

- Branch: `cleaning` (already checked out)
- Do not change Schwab OAuth/token behavior
- Do not remove stream/scan in this batch
- `TRADING_MODE` remains `dry_run` | `live` only (no `paper`)
- `enable_orb_live` default **False** — blocks real ORB order submission when true would allow; dry_run already blocks broker
- Every PR must leave `pytest` green
- Do not implement Alpaca/runner_momentum
- Commit after each task with conventional messages

---

### Task 1: PR1 — Capital safety + enable_orb_live

**Files:**
- Modify: `src/bot/config.py` — add `enable_orb_live: bool = Field(default=False, env="ENABLE_ORB_LIVE")`
- Modify: `src/bot/executor.py` and/or `src/bot/main.py` — short-circuit real ORB order submits when `enable_orb_live` is False (even if TRADING_MODE=live)
- Modify: `tests/unit/test_config.py` — assert default False; env override True works
- Add or modify: tests for executor/main short-circuit if needed
- Modify: `CLAUDE.md`, `README.md` — TRADING_MODE dry_run|live truth; lab cutover checklist (dry_run on server, verify `/api/status`, keep token + paper cron, healthcheck is auth-based)
- Modify: design doc already in tree — ensure committed with this or separate docs commit if not yet committed

**Acceptance:**
- Default `BotConfig().enable_orb_live is False`
- With `TRADING_MODE=live` and `ENABLE_ORB_LIVE=false`, ORB path does not place broker orders
- Docs state operator should set server `TRADING_MODE=dry_run` for cutover
- pytest green for config (+ any new tests)

---

### Task 2: PR2 — Remove sgt-surge deploy leftovers; fix healthcheck

**Files:**
- Delete: `deploy/com.jacobmadsen.sgt-surge.plist`
- Delete: `deploy/sgt-surge.service`
- Delete: `deploy/supervisor.conf`
- Delete empty dirs if present: `config/strategies/`, `tests/integration/`, `tests/backtest/` (remove `.gitkeep` if any; rmdir)
- Rewrite: `scripts/healthcheck.sh` for `sgt-schwab-bot` / `ut.gitsum.rest` / curl `/api/status` — not sgt-surge

**Acceptance:**
- No sgt-surge filenames under `deploy/`
- healthcheck references sgt-schwab / ut.gitsum.rest status endpoint
- pytest still green

---

### Task 3: PR3 — Archive dead scripts; drop killed strategies

**Delete (paired):**
- `scripts/backtest_hmm.py`
- `scripts/regime_terminal.py`
- `scripts/backtest_surge.py`
- `scripts/backtest_vwap_vs_macd.py`
- `scripts/backtest_diagnose.py`
- `scripts/backtest_today.py`
- Prefer delete both: `scripts/research/runners/runner_strategy.py` + `tests/unit/research/runners/test_runner_strategy.py` if not imported by prod
- `scripts/research/setups/sneaky_pivot.py` + `tests/unit/research/test_setup_sneaky_pivot.py`
- Remove sneaky_pivot from `scripts/research/run_harness.py` ALL_SETUPS

**Update (not delete function body if tests need archive — prefer remove CLI option):**
- `overnight_drift` as CLI option in `scripts/research/swing/run_swing.py` — remove from choices; keep function in strategies.py only if still imported by tests — **update tests** in same commit so pytest green
- Fix `src/__init__.py` tastytrade docstring → Schwab/lab
- Grep and scrub leftover references that break imports

**Acceptance:**
- `pytest` green
- No production imports of deleted modules
- `overnight_drift` not a CLI strategy choice
- `sneaky_pivot` not in harness

**Keep:** `scripts/backtest_orb.py` (design says keep as legacy research)

---

### Task 4: PR4 — Dependency slim

**Files:**
- Modify: `requirements.txt` — remove `streamlit`, `plotly`, `sqlalchemy` (and any section comments that only served them)
- Grep codebase for imports of those packages — must be zero after PR3

**Acceptance:**
- `grep -rn 'streamlit|plotly|sqlalchemy' --include='*.py' --include='requirements.txt'` shows no production imports (docs/design OK)
- pytest green

---
