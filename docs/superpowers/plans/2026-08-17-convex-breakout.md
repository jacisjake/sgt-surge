# Convex Breakout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the 2026-08-06 convex-breakout risk model and low-price universe so the live $194 book can flatten the eight mega-caps at the 2026-08-18 open and take new 52w-breakout entries from `liquid_lowprice.txt`.

**Architecture:** Keep `is_fresh_breakout` and `Breakout52wStrategy.plan`. Replace the 8% stop + SMA50 exit with ATR-clamped initial stop + chandelier trail. Persist `entry_date` + `initial_stop` in live audit `position_meta`. Point paper/live experiments at the screened $3–25 universe. Flatten the current mega-cap book at the open with a one-shot script.

**Tech Stack:** Python 3.11, pytest, pandas, Schwab API, TradingView screener, host cron.

**Spec:** `docs/superpowers/specs/2026-08-06-convex-breakout-design.md`

## Global Constraints

- k₁ default 2.0, k₂ default 3.0, ATR period 14, stop clamp [4%, 15%] — center of the spec grid. Sweep is deferred; do not block the open on it.
- No profit target. SMA50 exit off unless `use_ma_exit=true`.
- Cash account T+1: sell at the open; next buys may use unsettled cash if held past settlement.
- Live money: flatten preview first, then `--live` only on the scheduled 9:30 ET run.
- `ENABLE_ORB_LIVE` stays false. ORB bot must not attach 5-min ATR take-profits to swing positions.
- Restart only `sgt-schwab-bot`. Never `podman stop -a`.

---

### Task 1: ATR stop distance

**Files:**
- Modify: `src/lab/strategies/_common.py`
- Test: `tests/unit/lab/test_atr_stops.py`

**Interfaces:**
- Produces: `true_range_atr(df, period=14) -> pd.Series`, `atr_stop_distance(atr, entry, k1, lo=0.04, hi=0.15) -> float`, `chandelier_floor(highest_high, atr, k2) -> float`

- [ ] Write failing tests for clamp + true-range gap term
- [ ] Implement helpers
- [ ] Tests pass

### Task 2: Strategy ATR entry + chandelier exit

**Files:**
- Modify: `src/lab/strategies/breakout_52w.py`
- Modify: `src/lab/fills/sim.py` (treat `trail`/`gap_stop` as stop fills)
- Test: `tests/unit/lab/test_breakout_52w_plan.py`

**Interfaces:**
- Consumes: Task 1 helpers
- Produces: `plan()` BUY `stop_price = entry * (1 - clamp(k1*ATR/entry))` when `k1` set; SELL reasons `stop` / `gap_stop` / `trail`; no SMA50 unless `use_ma_exit`

- [ ] Failing tests for ATR sizing, clamp, trail, gap, no target, no SMA50
- [ ] Implement
- [ ] Existing plan tests still pass when `k1` omitted (legacy 8% stop)

### Task 3: Position state + journal

**Files:**
- Create: `src/lab/journal.py`
- Modify: `src/lab/runners/live.py` (`broker_to_views`, buy audit meta)
- Modify: `src/lab/fills/sim.py` (R-multiple + regime on close)
- Test: `tests/unit/lab/test_journal.py`, `tests/unit/lab/test_live_runner.py`

**Interfaces:**
- Produces: `r_multiple(entry, exit, initial_stop)`, `append_closed_trade(path, record)`
- Live `position_meta[sym] = {entry_date, initial_stop, entry_price}`
- `broker_to_views` uses stored `initial_stop` and `entry_date`, never `as_of` when meta exists

### Task 4: Wire universe + live_swing + ORB guard

**Files:**
- Modify: `config/experiments.yaml`
- Modify: `scripts/live_swing.py` (pass k1/k2; persist/read position meta if used)
- Modify: `src/lab/runners/paper.py` and `src/lab/runners/live.py` (fetch bars for open symbols not in universe)
- Modify: `src/bot/main.py` (`_add_default_stops` no-op when `enable_orb_live` is false)
- Test: existing live_swing + a new ORB-skip test

### Task 5: Flatten script + generate universe + deploy

**Files:**
- Create: `scripts/flatten_positions.py` (preview default, `--live` places sells)
- Ops: `python -m scripts.lab.build_universe` on the server
- Ops: deploy, schedule `30 9 * * 2` one-shot flatten for 2026-08-18, point live cron at low-price file

---

## Deferred (do not block the open)

- k₁×k₂ skew sweep on the new universe
- SMTP alerting
- Resting broker chandelier orders
