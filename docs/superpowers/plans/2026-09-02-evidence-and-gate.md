# Evidence Integrity and the Live Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the live book from destroying its own trade evidence, and make the promote gate actually gate the path that spends money.

**Architecture:** Three independent defects on the live path, each fixed in place with tests. (1) Exits that do not go through `live_swing`'s sell branch leave orphaned audit meta and no journal record — reconcile against the broker each run and journal what is dropped. (2) `flatten_positions.py` is an exit path that records nothing — give it the same journalling. (3) `live_swing --live` consults only `TRADING_MODE`, never the registry stage — enforce `assert_can_run` with an explicit, recorded override.

**Tech Stack:** Python 3.12, pytest, pandas, Schwab API (schwab-py), host cron.

**Spec:** `docs/superpowers/specs/2026-08-06-convex-breakout-design.md` (§Measurement, §Accepted risks)

## Global Constraints

- Real money is live: equity $192.52, one open position (FRSH, entry 2026-08-28 @ 13.86, initial_stop 12.8332).
- `ENABLE_ORB_LIVE` stays false. Restart only `sgt-schwab-bot`; never `podman stop -a`.
- Preview remains the default for every script that can place orders.
- No exit price is recoverable for already-closed positions — do not fabricate one. An unrecoverable trade is recorded with nulls, never with a guessed value.
- Stage machine is `research → live`. `breakout_52w_live` is currently `stage: research` with no backtest report.
- The journal wiring (commit 1501dfd) is **not yet deployed** to the server.

---

### Task 1: Reconcile audit meta against broker positions

Audit meta currently holds 18 symbols while the broker holds 1. Positions that
exit outside `live_swing`'s sell branch leave their meta behind forever and are
never journalled, so the forward evidence base silently drains away.

**Files:**
- Modify: `scripts/live_swing.py` (add `reconcile_audit_meta`, call it in `_run`)
- Test: `tests/unit/test_live_swing_reconcile.py`

**Interfaces:**
- Produces: `reconcile_audit_meta(position_meta, held_symbols, journal_path, today) -> list[dict]`
  — journals then drops meta for any symbol no longer held; returns the dropped records.

- [ ] **Step 1: Write the failing tests**

```python
import datetime, json
from scripts.live_swing import reconcile_audit_meta


def test_meta_for_a_symbol_no_longer_held_is_journalled_and_dropped(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {
        "FRSH": {"entry_date": "2026-08-28", "entry_price": 13.86, "initial_stop": 12.83},
        "IOVA": {"entry_date": "2026-08-20", "entry_price": 9.68, "initial_stop": 8.83},
    }
    dropped = reconcile_audit_meta(meta, {"FRSH"}, journal, datetime.date(2026, 9, 2))

    assert list(meta) == ["FRSH"]          # still held, untouched
    assert len(dropped) == 1
    rows = json.loads(journal.read_text())
    assert rows[0]["symbol"] == "IOVA"
    assert rows[0]["reason"] == "reconciled_unknown_exit"
    assert rows[0]["exit_price"] is None   # unrecoverable, never guessed
    assert rows[0]["r_multiple"] is None
    assert rows[0]["entry_price"] == 9.68  # what we do know is preserved


def test_nothing_dropped_when_every_symbol_is_still_held(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {"FRSH": {"entry_date": "2026-08-28", "entry_price": 13.86, "initial_stop": 12.83}}
    dropped = reconcile_audit_meta(meta, {"FRSH"}, journal, datetime.date(2026, 9, 2))
    assert dropped == []
    assert not journal.exists()


def test_reconcile_is_idempotent(tmp_path):
    journal = tmp_path / "journal.json"
    meta = {"OLD": {"entry_date": "2026-08-01", "entry_price": 5.0, "initial_stop": 4.6}}
    reconcile_audit_meta(meta, set(), journal, datetime.date(2026, 9, 2))
    reconcile_audit_meta(meta, set(), journal, datetime.date(2026, 9, 2))
    assert len(json.loads(journal.read_text())) == 1   # not double-written
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_live_swing_reconcile.py -v`
Expected: FAIL with `ImportError: cannot import name 'reconcile_audit_meta'`

- [ ] **Step 3: Implement**

```python
def reconcile_audit_meta(position_meta, held_symbols, journal_path, today) -> list[dict]:
    """Journal and drop meta for positions the broker no longer holds.

    A position can leave the book without passing through this script's sell
    branch — a manual close, the flatten script, or a missed run. Its meta
    would otherwise sit in the audit forever and the trade would vanish from
    the evidence base entirely. The exit price is not recoverable after the
    fact, so it is recorded as null rather than guessed.
    """
    from src.lab.journal import append_closed_trade

    held = {s.upper() for s in held_symbols}
    dropped: list[dict] = []
    for sym in [s for s in position_meta if s.upper() not in held]:
        meta = dict(position_meta.pop(sym) or {})
        dropped.append(append_closed_trade(journal_path, {
            "symbol": sym,
            "entry_date": meta.get("entry_date"),
            "exit_date": today.isoformat(),
            "entry_price": meta.get("entry_price"),
            "exit_price": None,
            "qty": None,
            "initial_stop": meta.get("initial_stop"),
            "reason": "reconciled_unknown_exit",
            "regime": meta.get("regime"),
            "r_multiple": None,
        }))
    return dropped
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_live_swing_reconcile.py -v`
Expected: 3 passed

- [ ] **Step 5: Call it from `_run`, after positions are fetched and audit loaded**

In `scripts/live_swing.py::_run`, immediately after `audit = load_audit(...)`:

```python
    meta = audit.setdefault("position_meta", {})
    held = {str(p.get("symbol") or "").upper() for p in positions}
    orphans = reconcile_audit_meta(meta, held, Path(args.journal_path), today)
    if orphans:
        print(f"[RECONCILE] journalled and dropped {len(orphans)} stale meta "
              f"entries: {', '.join(o['symbol'] for o in orphans)}")
        if args.audit_path:
            save_audit(Path(args.audit_path), audit)
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/live_swing.py tests/unit/test_live_swing_reconcile.py
git commit -m "fix(live): journal and reconcile orphaned audit meta"
```

---

### Task 2: Journal the flatten script's sells

`flatten_positions.py` closes positions and records nothing — it is the exit
path that lost most of the 17 missing trades.

**Files:**
- Modify: `scripts/flatten_positions.py`
- Test: `tests/unit/test_flatten_positions.py` (extend)

**Interfaces:**
- Consumes: `record_closed_trades(results, position_meta, journal_path, today)` from Task 0 (already on `main` as of 1501dfd).

- [ ] **Step 1: Write the failing test**

```python
def test_flatten_journals_each_filled_sell(tmp_path, monkeypatch):
    import datetime, json
    from scripts.flatten_positions import journal_flatten_results

    journal = tmp_path / "journal.json"
    meta = {"ABC": {"entry_date": "2026-08-01", "entry_price": 10.0, "initial_stop": 9.0}}
    results = [{"status": "submitted", "action": "sell", "symbol": "ABC",
                "qty": 2.0, "price": 12.0}]
    journal_flatten_results(results, meta, journal, datetime.date(2026, 9, 2))

    rows = json.loads(journal.read_text())
    assert len(rows) == 1
    assert rows[0]["reason"] == "flatten"
    assert rows[0]["r_multiple"] == 2.0      # (12-10)/(10-9)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_flatten_positions.py -k flatten_journals -v`
Expected: FAIL with `ImportError: cannot import name 'journal_flatten_results'`

- [ ] **Step 3: Implement**

```python
def journal_flatten_results(results, position_meta, journal_path, today) -> list[dict]:
    """Record every filled flatten sell so the trade is not lost."""
    from scripts.live_swing import record_closed_trades

    tagged = [{**r, "reason": "flatten"} for r in results]
    return record_closed_trades(tagged, position_meta, journal_path, today)
```

Then call it from the script's `--live` branch, after `execute_plan`, loading
the audit with `load_audit` and saving it back with the sold symbols removed.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_flatten_positions.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add scripts/flatten_positions.py tests/unit/test_flatten_positions.py
git commit -m "fix(live): journal flatten sells instead of dropping them"
```

---

### Task 3: Enforce the promote gate on the live path

`live_swing --live` is guarded only by `TRADING_MODE=live`. It never reads the
registry, so `stage: research` with no backtest report still places real orders.
This makes the entire promote gate decorative on the one path that spends money.

**Files:**
- Modify: `scripts/live_swing.py` (gate check + `--ignore-gate` flag)
- Test: `tests/unit/test_live_swing_gate.py`

**Interfaces:**
- Produces: `check_live_gate(experiment_id, *, trading_mode, enable_orb_live, git_path, override_path) -> str | None`
  — returns None when the gate permits live, else the refusal reason.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from scripts.live_swing import check_live_gate


def _reg(tmp_path, stage, mode="live"):
    import yaml
    p = tmp_path / "exp.yaml"
    p.write_text(yaml.dump({
        "version": 1,
        "experiments": {"x": {
            "strategy": "breakout_52w", "params": {}, "capital": 200.0,
            "mode": mode, "stage": stage,
            "symbols_file": str(tmp_path / "u.txt"),
            "ledger_path": str(tmp_path / "l.json"),
        }},
    }))
    (tmp_path / "u.txt").write_text("AAA\n")
    return str(p), str(tmp_path / "ov.yaml")


def test_gate_refuses_a_research_stage_experiment(tmp_path):
    git, ov = _reg(tmp_path, "research")
    reason = check_live_gate("x", trading_mode="live", enable_orb_live=False,
                             git_path=git, override_path=ov)
    assert reason is not None
    assert "stage" in reason.lower()


def test_gate_permits_a_promoted_experiment(tmp_path):
    git, ov = _reg(tmp_path, "live")
    assert check_live_gate("x", trading_mode="live", enable_orb_live=False,
                           git_path=git, override_path=ov) is None


def test_gate_refuses_when_orb_live_is_enabled(tmp_path):
    git, ov = _reg(tmp_path, "live")
    reason = check_live_gate("x", trading_mode="live", enable_orb_live=True,
                             git_path=git, override_path=ov)
    assert reason is not None


def test_gate_refuses_an_unknown_experiment(tmp_path):
    git, ov = _reg(tmp_path, "live")
    reason = check_live_gate("nope", trading_mode="live", enable_orb_live=False,
                             git_path=git, override_path=ov)
    assert "unknown experiment" in reason.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_live_swing_gate.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_live_gate'`

- [ ] **Step 3: Implement**

```python
def check_live_gate(experiment_id, *, trading_mode, enable_orb_live,
                    git_path="config/experiments.yaml",
                    override_path="state/experiments/overrides.yaml"):
    """Return None if the registry permits live orders, else the refusal reason.

    live_swing places real fractional orders. Without this the promote gate is
    decorative: a stage=research experiment with no backtest report would still
    trade real money.
    """
    from src.lab.registry import assert_can_run, load_registry

    try:
        reg = load_registry(git_path, override_path)
    except (FileNotFoundError, ValueError) as e:
        return f"registry unreadable: {e}"
    if experiment_id not in reg:
        return f"unknown experiment {experiment_id!r}"
    try:
        assert_can_run(reg, reg[experiment_id], "live", trading_mode,
                       enable_orb_live=enable_orb_live)
    except PermissionError as e:
        return str(e)
    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_live_swing_gate.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire into `_run`, immediately before placing orders**

Replace the `TRADING_MODE` check in `_run` with:

```python
    if cfg.trading_mode != TradingMode.LIVE:
        print(f"\nRefusing --live: bot trading_mode is {cfg.trading_mode.value}, not live.")
        return 1

    gate = check_live_gate(
        args.experiment_id,
        trading_mode=cfg.trading_mode.value,
        enable_orb_live=bool(cfg.enable_orb_live),
    )
    if gate and not args.ignore_gate:
        print(f"\nRefusing --live: promote gate says {gate}")
        print("Promote the experiment, or pass --ignore-gate --gate-reason '...' "
              "to override deliberately.")
        return 1
    if gate and args.ignore_gate:
        if not args.gate_reason:
            print("\n--ignore-gate requires --gate-reason.")
            return 1
        print(f"\n[GATE OVERRIDE] {gate} — proceeding: {args.gate_reason}")
        audit.setdefault("gate_overrides", []).append({
            "at": today.isoformat(), "gate": gate, "reason": args.gate_reason,
        })
```

Add the flags next to `--audit-path`:

```python
    ap.add_argument("--experiment-id", default="breakout_52w_live")
    ap.add_argument("--ignore-gate", action="store_true",
                    help="place orders even if the promote gate refuses (recorded)")
    ap.add_argument("--gate-reason", default=None,
                    help="required justification when --ignore-gate is used")
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/live_swing.py tests/unit/test_live_swing_gate.py
git commit -m "fix(live): enforce the promote gate before placing real orders"
```

---

## Operational consequence — read before deploying

After Task 3, the live cron **will refuse to place orders**, because
`breakout_52w_live` is `stage: research` with no backtest report. That is the
gate working as designed. To resume live trading, either:

1. Run the sweep, write the backtest report, and `promote --to live`; or
2. Add `--ignore-gate --gate-reason "..."` to the cron line, which is recorded
   in the audit under `gate_overrides`.

This is an operator decision, not a code decision. Do not pick one silently.
