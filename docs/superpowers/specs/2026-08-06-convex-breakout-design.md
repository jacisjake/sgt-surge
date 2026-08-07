# Convex Breakout — Design (2026-08-06)

Replaces `2026-08-04-live-strategy-split-design.md` (deleted — it was scaffolding,
not strategy).

## Objective

A strategy that **wins big some of the time and loses small**, on a ~$194 live
account, instrumented well enough to learn what works under what conditions.

Positive skew, not high win rate. Expect ~35% winners. The strategy is working if
a small number of trades produce large R-multiples while every loss stays ~1R.
**Total return and win rate are not the acceptance metrics** — a 60%-win-rate
strategy with capped upside is a failure by this objective.

## What changes

| | now | change to |
|---|---|---|
| entry | 52-week fresh breakout | **unchanged** |
| initial stop | fixed 8% | `k₁ × ATR14`, clamped to [4%, 15%] |
| trailing exit | none | **chandelier**: `highest_high − k₂ × ATR14` |
| profit target | none | **unchanged** — never cap the tail |
| trend exit | `close < SMA50` | **removed** |
| universe | 63 mega-caps + ETFs | `state/universes/liquid_lowprice.txt` |
| sizing | `risk_pct × equity / stop_pct` | `risk_pct × equity / (entry × stop_dist)` |
| runner | `scripts/live_swing.py` | **unchanged** — updated in place |
| trade record | prose log | structured journal with regime tags |

## Why each change

**Entry is not the problem.** `is_fresh_breakout` was reviewed: causal, correct,
and its second condition properly prevents re-entering an already-extended
breakout. Six weeks of losses came from the risk model.

**Fixed stop → ATR.** The ledger shows the winners being amputated:

| symbol | entry | stop-out | later |
|---|---|---|---|
| AMD | 6/15 @ 547.26 | 6/26 @ 503.48 | **6/30 @ 580.91** |
| GS | 6/17 @ 1099.14 | 6/30 @ 1011.21 | **7/14 @ 1140.00** |

Both were stopped out and then traded above their original entries within days —
then re-bought higher and stopped again. One stop width cannot serve both AMD and
KO. Sizing must move inversely to stop width or risk stops being 1R:

```
atr_pct       = ATR14(symbol) / entry_price
stop_dist     = clamp(k₁ × atr_pct, 0.04, 0.15)
stop_price    = entry_price × (1 − stop_dist)
qty           = (risk_pct × equity) / (entry_price × stop_dist)
```

The clamp is required: the bake-off's harness findings flagged tiny-stop
R-instability, where stops sitting cents from entry blow up the R denominator
(−2.7R artifacts on ETFs).

**Chandelier trail.** Already implemented and tested at
`scripts/research/sim.py:33` — ratcheting floor at `highest_high − k × ATR`,
never decreasing, gap-through fills at the open, and `target=None` support so
upside is uncapped. This is the mechanism that produces the right tail. Port it
into the strategy; do not rewrite it.

**Remove the SMA50 exit.** With a chandelier underneath, SMA50 fires *first* on
any sharp pullback and caps the runner — the same amputation as the 8% stop, one
step later. Chandelier alone is what lets a winner reach 5R.

**Low-price universe.** Mega-caps do not have the right tail. The screen
(price $3–25, ≥$5M median dollar volume, ≥280 bars) selects higher-beta names
where a breakout can actually run. Also fixes fractional-share granularity: above
$25 every fill is a fraction of a share.

## Parameter selection

Sweep `k₁ ∈ {1.5, 2.0, 2.5, 3.0}` × `k₂ ∈ {2.5, 3.0, 4.0}` on the day-step
backtest over the low-price universe.

**Select on skew, not P&L:** largest winner in R, payoff ratio, and share of
total P&L from the top 3 trades. Explicitly *not* total return or win rate —
that selection rule is what produced the +55% breakout_52w claim that failed
forward.

Accept a pair only if the surrounding grid cells are also sane. If no pair
produces a right tail, that is a finding: report it and stop rather than ship a
tuned number.

## Measurement

Per-trade journal (JSON, append-only), one record per closed trade:

- symbol, entry/exit date and price, qty
- **R-multiple** (`(exit − entry) / (entry − initial_stop)`)
- exit reason (`stop` / `gap_stop` / `trail`)
- **regime at entry**: SPY vs SMA200, and SPY's distance from it

Reported metrics: payoff ratio, max winner R, top-3 share of P&L, expectancy in
R, and expectancy split by regime. If no trade ever exceeds 3R, the trail is not
working regardless of the P&L line.

## Rollout

0. **Generate the universe** — `state/universes/` does not exist on the server;
   `build_universe` has never been run there. Prerequisite for everything else.
1. Tests first (TDD, per repo norms) — §Testing.
2. Implement: ATR stop + sizing, chandelier port, SMA50 removal, trade journal.
3. Run the k sweep; record the chosen pair and the surface.
4. Sell all 8 mega-cap positions (~$194 freed).
5. Deploy; point `live_swing.py` at the low-price universe.
6. Verify with a preview run before the first live cron fires.

**Rollback:** revert the commit and restore the previous cron line.

## Testing

- **ATR sizing:** risk stays ≈ `risk_pct × equity` across a range of ATR values;
  clamp binds at both ends.
- **Chandelier:** ratchets up, never down; never below initial stop; gap-through
  fills at the open; uncapped upside with `target=None`.
- **No target:** a position that runs 10R is never exited by a target.
- **Journal:** R-multiple and regime tag recorded correctly on close.
- **Universe:** screen honors price band, dollar-volume floor, and bar count.

## Accepted risks

1. **No forward evidence.** The new risk model goes live off a backtest sweep.
   Mitigated by selecting on skew rather than return, but unproven forward.
2. **No alerting.** SMTP is unconfigured; every run logs
   `[ALERT] SMTP not configured — dropping alert`. Failures are found by looking.
3. **Software stops during token outages.** Schwab refresh tokens expire every 7
   days with no API renewal — a platform constraint. Stops are evaluated once
   daily by the runner; there are no resting broker orders. When the token dies,
   open positions stop being evaluated at all.
4. **Higher-beta universe.** Low-price names gap harder. Wider ATR stops size
   positions down to compensate, but gap risk through the stop is real and is the
   cost of buying the right tail.

## Scrapped

- **`short_term_reversal`** — wrong payoff shape. 58% win rate with a +10% target
  hard-caps every winner; it wins often and small, the opposite of the objective.
  Its backtest was the best in the bake-off, which is why it was nearly chosen —
  best expectancy and right shape are different questions.
- **Capital split, ledger-owned positions, promote-gate integration, LiveRunner
  migration** — machinery for a $194 account. One strategy means one owner, so
  ownership tagging solves a problem that no longer exists.
