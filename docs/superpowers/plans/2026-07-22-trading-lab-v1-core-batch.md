# Trading Lab v1 — Core Batch (PRs 5–7)

**Goal:** Shared Strategy protocol, Breakout52w.plan, SimFill ≡ paper_forward.step, registry + PaperRunner with dual-run parity.

**Design:** `docs/superpowers/specs/2026-07-22-trading-lab-v1-design.md` §§1–2, 4, 8–10, PR Plan 5–7.

**Land 5–7 in one tight window** — dual-maintain breakout logic only briefly.

## Global Constraints

- Branch: `cleaning`
- SimFill formulas MUST match `paper_forward.step` exactly (`slip = 2*bps/1e4`, raw prices, ratio PnL)
- Dual-run: pnl/cash/realized within **$0.01**; same symbols/reasons/dates
- No LiveRunner / promote CLI in this batch (PR8–9 later)
- No short_term_reversal strategy module yet (PR10)
- Keep paper_forward CLI and run_paper_forward.sh working via shim
- pytest unit suite green

---

### Task 5: PR5 — Protocol + _common + Breakout52wStrategy

**Create:**
- `src/lab/__init__.py`
- `src/lab/protocol.py` — Side, OrderIntent, PositionView, PortfolioView, MarketContext, Strategy Protocol (per design §1)
- `src/lab/strategies/__init__.py` — STRATEGY_REGISTRY with breakout_52w only for now
- `src/lab/strategies/_common.py` — `stop_fill_price`, `build_risk_on`, `is_fresh_breakout` (canonical)
- `src/lab/strategies/breakout_52w.py` — `Breakout52wStrategy.plan`

**Wire facades (no behavior change):**
- `scripts/research/swing/strategies.py` — re-export stop_fill_price, build_risk_on from `_common` (keep other pure strategies)
- `scripts/research/swing/paper_forward.py` — import is_fresh_breakout from `_common`; keep step() for now OR implement decisions via strategy.plan then apply same fills (preferred if tests pass)
- `scripts/live_swing.py` — import is_fresh_breakout from `_common`; plan_orders may call Breakout52wStrategy.plan + convert intents → plan dicts

**Tests:**
- `tests/unit/lab/test_breakout_52w_plan.py` — decision parity vs known cases (stop, trend_break, risk_off, fresh_breakout)
- Existing paper_forward + live_swing tests must still pass

**Commit:** `feat(lab): Strategy protocol + Breakout52w.plan + shared helpers`

---

### Task 6: PR6 — SimFill + ledger helpers

**Create:**
- `src/lab/fills/__init__.py`
- `src/lab/fills/sim.py` — apply OrderIntents to paper state using exact step formulas
- `src/lab/ledger.py` — new_state, load, save, append equity_curve_daily snapshot, paper PositionView adapter

**Wire:**
- `paper_forward.step` becomes thin wrapper: build portfolio/market → strategy.plan → SimFill.apply (preserve step signature)

**Tests:**
- Dual-run: step vs lab path on fixtures within $0.01
- equity_curve_daily appended on step

**Commit:** `feat(lab): SimFill ledger matching paper_forward.step`

---

### Task 7: PR7 — Registry + PaperRunner + shims

**Create:**
- `config/experiments.yaml` — breakout_52w_paper + optional str research stub without STR strategy if not ready
- `src/lab/registry.py` — load yaml, merge overrides, assert_can_run (paper modes only for now)
- `src/lab/runners/paper.py` — PaperRunner day step
- `src/lab/runners/experiment.py` — ExperimentRunner paper path
- `scripts/lab/__init__.py`, `scripts/lab/run_experiment.py`
- Add `pyyaml` to requirements if missing

**Shims:**
- `run_paper_forward.sh` → can call `python -m scripts.lab.run_experiment --id breakout_52w_paper` OR keep paper_forward which uses lab under the hood
- paper_catchup / web.py: resolve ledger via legacy_ledger_path (still swing_paper_breakout.json)
- Dual-run acceptance tests

**Commit:** `feat(lab): experiments.yaml + paper ExperimentRunner + shims`

---
