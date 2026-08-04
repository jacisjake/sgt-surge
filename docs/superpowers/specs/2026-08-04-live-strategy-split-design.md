# Live Strategy Split — Design (2026-08-04)

Run `breakout_52w` and `short_term_reversal` live side by side on a ~$194 cash
account, split ~$96 / ~$96, through the lab `LiveRunner`. Retire
`scripts/live_swing.py`. Fix breakout's risk model. Restore the Caddy proxy.

## 1. Why

### 1.1 What is live today

The server runs a third crontab line that CLAUDE.md does not document:

```
5 14 * * 1-5 podman exec -w /app sgt-schwab-bot \
    python -m scripts.live_swing --symbols-file /app/state/breakout_universe.txt --live
```

14:05 MDT = 16:05 ET, after the close, so bars are complete — the timing is
correct. `/opt/sgt-schwab/.env` has `TRADING_MODE=live`, and the job places real
fractional orders. It has been doing so since at least 2026-07-31.

`scripts/live_swing.py` never reads `config/experiments.yaml`. Its only guard is
`cfg.trading_mode == TradingMode.LIVE`. The Trading Lab promote gate — described
in CLAUDE.md as *the* decision gate for going live — does not apply to it.

### 1.2 The gate would have said no

`promote --check` on 2026-08-03:

| gate | required | actual | ok |
|---|---|---|---|
| min_paper_trading_days | 40 | 8 | ✗ |
| min_closed_trades | 15 | 10 | ✗ |
| max_paper_drawdown | ≤ 0.20 | 0.0202 | ✓ |
| require_positive_expectancy | true | **−1.8037** | ✗ |

`breakout_52w_live` is still `stage: paper` with 0 trades, and no
`state/experiments/overrides.yaml` exists on the server. Nothing was ever
promoted. Live runs purely because that cron line exists.

### 1.3 The strategy is losing for a diagnosable reason

Paper scoreboard: equity $181.96, total return **−9.02%**, expectancy
**−$1.80/trade**, 10 closed / 8 open, rolling 20-day mean −1.147%.

All 10 closed trades are losses. Expectancy of −$1.80 on ~$24 positions is
≈ −7.5% against an 8% stop, i.e. nearly every close was a stop-out. Ten mega-caps
falling 8% within days of a 52-week breakout, over 8 trading days, in a market
where SPY sits ~8% above its SMA200, is not a plausible market outcome.

The ledger shows what actually happened:

| symbol | entry | stop-out | re-entry |
|---|---|---|---|
| AMD | 6/15 @ 547.26 | 6/26 @ 503.48 | **6/30 @ 580.91** |
| GS | 6/17 @ 1099.14 | 6/30 @ 1011.21 | **7/14 @ 1140.00** |

Both names were stopped out and then traded *above their original entry* within
days. The breakouts were correct; the stop was too tight to survive them. This is
whipsaw caused by a **fixed 8% stop applied to every name regardless of
volatility** — placed inside normal daily noise for high-beta names (AMD, GS, C,
MS, INTC are 6 of the 10 losers) and far outside it for low-beta names.

`is_fresh_breakout` was reviewed and is correct and causal. The entry logic is
not the problem. The risk model is.

### 1.4 The one validated edge is on the bench

`docs/superpowers/results/2026-06-11-strategy-bakeoff.md` status line: *"one
validated edge found — short_term_reversal (swing)."*

- n=**303**, 58% win, **+0.47%/trade**, PF 1.38
- Slippage: +0.67% @5bps · +0.47% @15bps · **+0.17% @30bps** — positive at all levels
- Regime: **+0.45%, PF 1.32** over 2022-01→2024-01, including the 2022 bear
- Param grid: **all 12 combos positive**, monotonic gradient (deeper oversold →
  stronger bounce). An overfit or bug shows as one lucky cell; the surface is
  uniformly green.
- Locked config `down_days=3, hold=5` — the robust middle. The juicier
  `down_days=4` cell (+0.82%) was declined for n=125.

It is implemented (`src/lab/strategies/short_term_reversal.py`), registered as
`short_term_reversal_research`, and has never been run.

### 1.5 On VWAP

Recollection during design was that a VWAP strategy performed best. The record
does not support it. `vwap_reclaim` scored n=22, 27% win, +0.251R, PF 1.35 —
positive but second to `orb_clean` (+0.657R), on a run the bake-off stamps
*"Sample tiny. Directional only."* That document's own method note calls the
whole set (ORB-clean, VWAP-reclaim, first-pullback, PM-high-break) *"one paradigm
in four costumes"* — buy intraday strength on a pre-market gapper, long, flat at
close — and resets the search because of it. The other VWAP in this repo is the
`order_executor` fill-price fix, a pricing mechanism, not a strategy.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Run both strategies live, ~$96 / ~$96 | Operator choice. Diversifies across two edges. |
| D2 | Force STR to `stage: live` at full split now | Operator choice, backtest evidence only. |
| D3 | Ledger-owned positions | Fits existing structure, auditable, keeps universe breadth. |
| D4 | Retire `scripts/live_swing.py`; both via `LiveRunner` | Gets the split *and* closes the gate bypass in one step. |
| D5 | ATR-scaled stop for breakout_52w | Directly addresses the diagnosed whipsaw. |
| D6 | STR risk params frozen | Its credibility *is* the validated param grid. |
| D7 | Restore Caddy proxy | Weekly manual re-auth is mandatory and currently impossible. |
| D8 | SMTP alerting and resting broker stops out of scope | Operator choice. See §7. |

## 3. Architecture

### 3.1 Retire live_swing, run through LiveRunner

`live_swing.py` is hardcoded to breakout_52w and cannot run two strategies.
Teaching it the registry would mean rebuilding `LiveRunner`. Moving to
`LiveRunner` delivers the split and makes "force STR live" an explicit, audited
registry action instead of a flag on a cron line.

### 3.2 Ledger-owned positions

Both strategies read `client.get_positions()`, which returns the **whole
account**. Unguarded, STR would see breakout's JPM and apply its own
5%-stop / 10%-target / 5-day-time rules to it. The two strategies would fight
over each other's positions.

Rule: **a broker position belongs to an experiment only if that experiment's
ledger `open_positions` contains it.** Each strategy is shown only its own.
Positions owned by neither are logged as orphans and never touched.

### 3.3 Capital budgeting

Both strategies read the same broker `buying_power`. If each sees $96 free, each
deploys $96 and the account overdraws — this already happened; the 2026-07-31 log
shows `cash $-0.19`. Each experiment sizes against:

```
available = min(my_budget − my_deployed, broker_cash)
```

`my_deployed` comes from that experiment's own ledger. The `broker_cash` term is
the backstop that holds regardless of budget arithmetic.

## 4. Risk model

### 4.1 breakout_52w — ATR-scaled stop

```
atr_pct       = ATR14(symbol) / entry_price        # ATR as a fraction of price
stop_distance = clamp(k × atr_pct, 0.04, 0.15)     # fraction of entry price
stop_price    = entry_price × (1 − stop_distance)
qty           = (risk_pct × equity) / (entry_price × stop_distance)
```

All stop widths are expressed as a **fraction of entry price**, so the clamp
bounds and `stop_pct` remain directly comparable to the current fixed 0.08.

The second line is what is missing today and is the point of the change: with a
variable stop width, size must move inversely or risk stops being constant.
High-vol names get wider stops and smaller positions; low-vol names get tighter
stops and larger positions. Risk stays pinned at 1%.

The clamp is required, not cosmetic. The bake-off's harness findings flagged
**tiny-stop R-instability** — structural stops sitting cents from entry blow up
the R denominator and produced −2.7R artifacts on ETFs. The floor prevents that;
the ceiling stops one position eating the budget.

**`k` is chosen by backtest sweep, not asserted.** Sweep `k ∈ {1.5, 2.0, 2.5,
3.0, 3.5}` on the day-step engine. Accept a value only if the whole surface is
sane — the same anti-overfit standard that makes STR trustworthy.

If no value produces positive expectancy, that is a finding, not a blocker to
engineer around: report it and stop. Shipping a tuned `k` picked from a
mostly-negative surface would repeat the mistake that put breakout_52w live on a
backtest that did not transfer. In that case the split decision returns to the
operator.

### 4.2 short_term_reversal — frozen

`down_days=3, hold=5`, stop 5%, target 10%, MA200, unchanged. The +0.47% is
credible *because* the 12-combo grid came back green on these exact values.
ATR-ifying STR would discard the only validated edge and leave two untested
strategies.

### 4.3 Both strategies

- **Position caps.** 4 slots each, declared explicitly. Today the entry loop
  fills until cash runs out — that is how the book reached 52% financials.
- **Deterministic ranking.** With 63 names and 4 slots, more signals fire than
  can be taken, and the current loop takes them in **dict iteration order**.
  - STR: rank by **depth of oversold** (cumulative decline across the down-days).
    Evidence-backed — the research grid showed deeper oversold → higher win% and
    expectancy, monotonically.
  - breakout: rank by **freshest break** (smallest % above the 252-day high).
- **Universe.** Drop SPY, QQQ, IWM from the tradeable set — do not take positions
  in your own regime filter. Sector ETFs (XLF, XLE, XLK, XLV) stay.

### 4.4 Out of scope

Sector / correlation caps. The 52% financials concentration is real but needs
sector metadata the repo lacks, and 4 slots each bounds the damage. Follow-up
issue, not this change.

## 5. Capital allocation & transition

Account as of 2026-08-04: equity **$194.45**, cash **$0.65**, 8 positions
totalling **$193.80**. Fully deployed — STR cannot open anything until capital is
freed. The rebalance is a prerequisite, not a tidy-up.

| Sell (~$98.40) | Keep (~$95.40 ≈ budget) |
|---|---|
| JPM $25.65 | AMZN $22.83 |
| BAC $25.41 | V $25.02 |
| XLF $25.07 | KO $24.44 |
| XLV $22.27 | ABBV $23.11 |

Selling JPM + BAC + XLF dissolves the financials cluster (4 names → 1); selling
XLV removes the healthcare ETF overlapping ABBV. What remains is four
uncorrelated names — consumer/tech, payments, staples, healthcare — at
approximately the $96 budget, filling exactly its 4 slots. Frees **$99.05** for
STR.

**Ledger seeding.** Because ownership is ledger-based,
`breakout_52w_live`'s ledger must be seeded with the four retained positions or
`LiveRunner` treats them as orphans and never manages them — no stops, no exits,
silently. Entry dates are reconstructed from `live_swing.log`; the broker returns
average price but not entry date. breakout_52w exits use only stop and SMA50, not
entry date, so residual gaps are tolerable.

**Legacy sizing.** The four inherited positions were sized under the old fixed-8%
rule. Their stops are recomputed under ATR, but notionals stay legacy, so risk is
not exactly 1% on them until they turn over. Only new entries are correctly sized
from day one.

## 6. Rollout

Order of operations:

1. Tests first (TDD, per repo norms) — §8.
2. Rebalance sells (4 orders).
3. Seed both ledgers.
4. Registry changes: `breakout_52w_live` capital 96 + ATR params; new
   `short_term_reversal_live` capital 96.
5. `promote short_term_reversal_live --to live --force --reason "..."` — writes a
   `live_audit.json` entry recording that it went live on backtest evidence with
   no forward confirmation.
6. Replace the `scripts.live_swing --live` cron line with two sequential
   registry-driven `LiveRunner` invocations, one per experiment, in a single
   wrapper script at the repo root (so `deploy-remote.sh`'s `rsync --delete`
   preserves it, as `run_paper_forward.sh` already does):

   ```
   run_experiment --id breakout_52w_live          --live
   run_experiment --id short_term_reversal_live   --live
   ```

   Sequential, not parallel — the second run must observe the first run's ledger
   writes so the shared `broker_cash` backstop (§3.3) sees accurate deployment.
   `run_paper_forward.sh` continues untouched.
7. Restore the Caddy proxy; verify `/api/status` returns JSON and the Schwab
   authorize flow loads.
8. Verify: preview run shows both strategies planning against their own ledgers
   and budgets, and neither emits intents for the other's symbols.

**Rollback.** Demote STR to `research` and restore the previous cron line. Ledger
records make the transition reversible.

## 7. Accepted risks

Recorded deliberately; each was raised and consciously accepted.

1. **STR goes live with zero forward evidence.** This is structurally the same
   bet that put breakout_52w at 0-for-10 — a strong backtest, no forward
   confirmation, real money. STR's evidence is materially better (kill-tested
   across slippage, regime, and a 12-cell param grid), but it remains untested
   forward. Full ~$96 from day one.
2. **No alerting.** Every run logs `[ALERT] SMTP not configured — dropping
   alert`. Two strategies will move real money unattended with no notification
   path. Order confirmations, run failures, token expiry, and ledger staleness
   all fail silently. Failures are discovered by noticing them.
3. **Software stops during token outages.** Schwab refresh tokens expire every 7
   days and cannot be renewed via API — a platform constraint, not a bug. Stops
   are evaluated once daily by the runner, with no resting broker orders. When
   the token dies, open positions stop having their stops evaluated at all. With
   the split this exposes 8 positions instead of 4. `OrderExecutor.
   execute_stop_limit_order()` already exists and would mitigate this, but
   whether Schwab accepts stop orders on *fractional* quantities is unknown and
   untested — `scripts/fractional_probe.py` is the precedent for settling it.
4. **Legacy sizing** on the four inherited positions until they turn over (§5).
5. **STR universe deviation.** Research ran on 17 liquid large-caps/ETFs; the
   registry points STR at the 63-name list. Same character, more candidates; the
   ranking rule (§4.3) makes the surplus useful rather than arbitrary.

## 8. Testing

TDD, per repo norms. New logic requiring coverage:

- **Ownership:** a position in experiment A's ledger produces no intent from
  experiment B; orphan positions produce no intent from either.
- **Budgeting:** two experiments cannot jointly deploy more than `broker_cash`;
  an experiment cannot exceed `my_budget`.
- **ATR sizing:** risk stays ≈ `risk_pct × equity` across a range of ATR values;
  clamp binds at both ends.
- **Ranking:** with more signals than slots, selection is deterministic and
  ordered by the specified key.
- **Position caps:** never exceeds 4 open per experiment.
- **STR regression:** frozen params still produce the research-matching intents.

## 9. Open items

- `k` for the ATR stop — resolved by sweep during implementation (§4.1).
- Entry dates for seeded positions — reconstructed from `live_swing.log`;
  unreconstructable ones documented in the ledger rather than fabricated.
