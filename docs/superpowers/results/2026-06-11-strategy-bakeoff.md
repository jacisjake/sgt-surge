# Strategy Bake-off — Results & Findings (2026-06-11)

Record of the data-driven strategy search. Goal: a long-only, cash-account-viable
edge with positive expectancy that survives costs and regime change. All backtests
run against Schwab historical bars via `scripts/research/`. Expectancy in **R** for
the intraday engine, in **% per trade** for the swing engine.

> **Status: one validated edge found** — `short_term_reversal` (swing). Not yet
> account-sized or forward-tested. No real money committed.

## Method note: we initially anchored

The first candidate set (ORB-clean, VWAP-reclaim, first-pullback, PM-high-break)
was **one paradigm in four costumes** — "buy intraday strength on a pre-market
gapper, long, flat by close" — i.e. the existing ORB bot. A real bake-off needs
structurally different bets. The reset (thesis-first, spanning universes/edges/
holding periods) is what surfaced the swing edges below.

## Run 1 — intraday gapper momentum (5-min, ~42 gappers, 2026-02-12→06-10, 15bps)

| setup | n | win% | exp(R) | PF | note |
|---|---|---|---|---|---|
| orb_clean | 18 | 22% | **+0.657** | 2.09 | tight stop = breakout-bar low (not OR low) |
| vwap_reclaim | 22 | 27% | +0.251 | 1.35 | |
| pm_high_break | 14 | 29% | -0.073 | 0.86 | |
| first_pullback | 18 | 22% | -0.124 | 0.86 | |
| sneaky_pivot | 0 | — | — | — | never fires (see finding) |

Sample tiny (26 setup-days, all low-N). **Directional only.** The key fix vs the
old live ORB: stop at the **breakout-bar low** (tight), not the OR low — that wide
OR-low stop is what caused the old 15%-max-stop-gate rejections.

## Run 2 — Sneaky Pivot on rangy ETFs (8 ETFs, 2026-04-01→06-10)

| setup | n | win% | exp(R) | PF |
|---|---|---|---|---|
| sneaky_pivot (long) | 47 | 38% | **-0.293** | 0.49 |
| momentum setups | 280–365 | ~22% | -0.97 to -3.80 | 0.13–0.24 |

`sneaky_pivot` long side has **no edge** even on its native (rangy) turf — winners
average < 1R because the prior-day-high target is too close after the bounce. The
momentum setups are catastrophic on ETFs (wrong instrument), amplified by the
tiny-stop instability below.

## Run 3 — swing (daily, 17 liquid large-caps/ETFs, 2024-06→2026-06)

| strategy | n | win% | exp(%) | PF | verdict |
|---|---|---|---|---|---|
| short_term_reversal | 303 | 58% | **+0.47%** | 1.38 | ✅ validated |
| overnight_drift | 8602 | 36% | -0.21% | 0.60 | ❌ dead after costs |

`overnight_drift`: breakeven at 5bps (-0.01%), the edge is smaller than the spread.
**Dropped.**

## Kill-test — `short_term_reversal` (buy 3-down-days dip while above 200-MA; exit on 5%-stop / 10%-target / 5-day time)

- **Slippage:** +0.67% (5bps) · +0.47% (15) · **+0.17% (30, pessimistic)** — all positive.
- **Regime:** **+0.45%, PF 1.32** over 2022-01→2024-01 (includes the 2022 bear) — not a bull-market fluke.
- **Param grid (12 combos, down_days × hold + stop/target):** **all 12 positive**, with a sensible monotonic gradient — deeper oversold (more down-days) → stronger bounce (win% & expectancy rise, n falls). A fit or bug would show one lucky cell; the whole surface is green.

**Working config locked: `down_days=3, hold=5`** (n=303, +0.47%, PF 1.38) — middle
of the grid, robust both ways. The juicier `dd=4` cell (+0.82%) is *not* chosen —
n=125 is too small to trust over the robust middle.

## Harness findings (improvements identified)

1. **Tiny-stop R-instability** — structural stops can sit cents from entry on calm
   5-min bars, so the R-denominator is tiny and noise/gap-through fills blow through
   multiple R (avgL of −2.7 to −5.6R on ETFs). **Fix: minimum-stop floor**
   (stop ≥ max(structural, k·ATR or x% of price)).
2. **`max_drawdown` metric is unreliable** for high-n / equal-weight trade lists —
   summing N unit-trades ≠ a real account path. **Fix: fractional position-sizing
   portfolio model** (in progress).
3. **Strategy↔universe fit is first-order** — Sneaky Pivot (mean-reversion to prior
   range) can't fire on gap-ups; momentum setups die on rangy ETFs. Match the edge
   to the regime, don't force it.

## Execution note

The live executor (`order_executor.py:279`) rounds to whole shares with a comment
that the Schwab trading API rejects fractional orders. User states fractional is
available. **Pre-live: confirm fractional is API-executable** (then drop the
`int()` rounding) or it's manual-only. Sizing model assumes fractional.

## Verdict & next steps

- **Winner:** `short_term_reversal` (long, swing, liquid large-caps/ETFs). Validated
  on costs + regime + params; **not** yet account-sized or forward-tested.
- **Drop:** `overnight_drift` (cost), `sneaky_pivot` (no edge / universe mismatch).
- **Revisit:** `orb_clean` / `vwap_reclaim` on gappers — promising but sample too
  small; need a bigger universe + the min-stop floor.
- **Next:** (1) fractional position-sizing + real-drawdown model → know the true
  equity curve & worst losing streak; (2) wire `short_term_reversal` into the bot in
  `dry_run` for out-of-sample forward testing; (3) confirm fractional execution.
  **No real money until it proves out forward.**

---

## Update (2026-06-12) — broadened the search → a better winner

Tunnel-vision check: the above stayed inside the mean-reversion family. Broadened to
distinct mechanisms, blind-tested on a 65-name sector-diverse universe (1% risk).

**Run 4 — distinct archetypes (blind, broad universe):**

| strategy | mechanism | bull 24-26 | maxDD | note |
|---|---|---|---|---|
| index_rsi2 | index mean-reversion | +3.5% | 3.5% | real but tiny |
| turn_of_month | calendar | -6.2% | 14.9% | dead |
| **breakout_52w** | **new-high momentum** | **+48–55%** | **~10%** | **winner** |

Also re-confirmed: **trend-following dip-buy FAILS the blind test** (−12.8% on broad,
the +1.5%/trade was hindsight curation). short_term_reversal deeper-streak (5 down,
hold 10) does hold blind: +14.6% / 6.6% DD at 1%.

**52-week-high breakout — full kill-test (warmup 2023, trades 2024-26, ~2yr, base
lookback 252 / exit<50ma / 1% risk):**
- Return **+55%**, maxDD **9.8%**, 77 trades.
- **Concentration: PASSED** — 34 distinct winning symbols, top-3 trades = 10% of gross
  profit, biggest winners are INTC/CAT/AMD/GOOGL (not AI darlings). Broad, not monsters.
- **Slippage:** +45.9% at 30bps.
- **Param robustness:** every variant positive — lookback 126 (+92%) & 252 (+55%),
  exit MA 20/50/100, stop. Base (252/50) chosen as robust middle (NOT the 126 cherry-pick).

**Caveat:** validated in the 2024-26 regime only (per direction to drop 2022). Bear
behavior unknown; the MA-exit trail is the built-in regime insurance.

**New verdict:** the edge is **momentum, expressed as systematic new-high breakouts** —
not buy-and-hold (tail risk) and not dip-buy (fails blind). `breakout_52w` (base config)
is the strategy to **forward-test** in dry-run. short_term_reversal remains a validated
lower-return/lower-DD fallback.
