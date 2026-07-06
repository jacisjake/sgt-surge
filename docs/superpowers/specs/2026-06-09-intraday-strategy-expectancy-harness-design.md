# Intraday Strategy Expectancy Harness — Design

**Date:** 2026-06-09
**Status:** Approved for spec review
**Author:** jake + Claude

## Goal

Replace the accreted ORB rule-stack (gates inherited from three prior strategy
eras) with a **data-driven choice of one intraday strategy** that has positive
expectancy — "win bigger than we lose over time." Rather than assert a strategy,
build a harness that measures the expectancy of several candidate setups on a
reconstructed historical sample of the gapper universe, ranks them, and lets the
numbers pick the winner.

Expectancy is the target metric:

```
expectancy(R) = win% · avg_win(R) − loss% · avg_loss(R)
```

## Context & constraints

- **Account:** Schwab **cash** account, ~$198 (started $270, goal $25,000).
- **Cash settlement (T+1):** the full balance can be deployed roughly **once per
  day** before cash is unsettled → effectively **~1 high-conviction trade/day**.
- **Holding period:** **intraday only, flat by 16:00 ET.** No overnight/gap risk.
  Per-trade edge must be strong (you only get ~1 swing/day).
- **Data:** Schwab price-history API. 1-min streamed live (aggregated to 5-min);
  for backtest we fetch historical 5-min and daily bars via REST.
- **Universe:** low-priced pre-market gappers (live: top-5 via TradingView).
- **Existing plumbing to reuse:** `SchwabClient` bar fetch, `Signal`/execution/
  `PositionManager`/risk layers, `scripts/backtest_orb.py` simulation patterns
  (entry-on-bar-close, chandelier trail).

## Non-goals

- Not changing the execution layer, OAuth, or dashboard.
- Not multi-day/swing setups (explicitly deferred — see holding-period decision).
- Not a portfolio/multi-position optimizer — one symbol, one trade/day.
- Not auto-promoting a winner to live trading; promotion is a manual gated step.

## The core problem: reconstructing the historical universe

The live bot selects top-5 pre-market gappers from TradingView at runtime. We have
**no historical snapshots** of that list, so a naive backtest can't know which
symbols to trade on a past day. We reconstruct the universe **from price data**:

For each past trading day **D**, a symbol qualifies as a gapper if (computed from
historical OHLCV only — no look-ahead):

- `gap% = open(D) / close(D−1) − 1 ≥ GAP_MIN` (default **0.20**)
- `dollar_volume(D) = close(D) · volume(D) ≥ DOLLAR_VOL_MIN` (default **$3M**)
- `PRICE_MIN ≤ open(D) ≤ PRICE_MAX` (default **$1–$20**)

Rank qualifiers by `gap%` and take the **top N** (default **5**) to mirror the
live selector.

**Scan universe:** a cached list of US common stocks (seed from a static symbol
list / prior scanner results). Gap detection needs only **daily** bars, which
Schwab serves for years, so the scan is cheap and long-history.

**Known biases (documented, not silently ignored):**
- *Survivorship:* the scan list is today's symbols; delisted past gappers are
  missing. Acceptable for a directional read; noted in the report.
- *Selection fidelity:* data-defined gap% ≠ TradingView's exact ranking, but it's
  a reproducible, defensible proxy.

## Data layer

- **Daily bars** (gap scan): extend `SchwabClient` with
  `get_history(symbol, timeframe="1Day", start, end)` — schwab-py's
  `get_price_history_every_day(symbol, start_datetime, end_datetime)`. Current
  `get_bars` takes no date args; this adds a date-ranged fetch.
- **5-min intraday bars** (simulation): `get_history(symbol, "5Min", start, end,
  extended_hours=False)` per gapper-day. **Depth is the binding limit** — Schwab's
  minute/5-min history reaches back a few months; verify actual depth on build and
  report the realized sample size. Expected ~50–60 days × ~5 names ≈ **250–300
  setup-days**.
- **Extended-hours bars** (setup D only): `extended_hours=True` to get the
  pre-market high. If Schwab doesn't serve usable PM bars, **setup D is dropped**
  and the report says so.
- Cache fetched bars to disk (`state/backtest_cache/<symbol>/<date>.parquet`) so
  re-runs don't re-hit the API.

## The candidate setups

All intraday, evaluated bar-by-bar on 5-min bars, force-flat at the 15:55 ET bar.
OR-dependent setups (A) begin once the OR is established at 09:45 ET; setups that
don't need the OR (B, D) may begin at the 09:30 open. Each emits **at most one
trade per symbol per day** (first qualifying entry). Stops are **structural and tight** — the core fix that makes per-trade R
favorable and removes the need for the old 15%-max-stop gate.

Common interface: `Setup.evaluate(day_bars, ctx) -> Optional[Trade]`.

| Setup | Entry (first qualifying 5-min bar) | 1R = entry − stop | Exit |
|---|---|---|---|
| **A. ORB-clean** | close > OR-high (OR = 09:30–09:45 H) | breakout-bar **low** | chandelier `HH − k·ATR`; flat 15:55 |
| **B. VWAP reclaim** | close crosses back **above VWAP** and holds (low ≥ prior low) | reclaim-dip low | trail under VWAP / chandelier; flat 15:55 |
| **C. First pullback** | after opening drive ≥ 1·ATR, first **higher-low to 9-EMA**, enter on reclaim bar | pullback low (tightest) | measured-move (1× drive) target + trail; flat 15:55 |
| **D. PM-high break** | close > **pre-market high** | last swing low | trail; flat 15:55 (contingent on PM data) |

**Indicator context** (`ctx`, computed per day from that day's 5-min bars):
opening range (09:30–09:45 H/L/vol), session VWAP, EMA(9), ATR(14), pre-market
high, relative volume. Pure functions over the bar DataFrame.

## Simulation engine (fills & bias control)

Shared by all setups to keep them comparable:

- **Entry fill:** at the **signal bar's close** (consistent with existing ORB sim).
- **Slippage/spread:** configurable haircut `SLIP_BPS` (default **15 bps**) applied
  to entry and exit — low-priced gappers have wide spreads, so this materially
  affects expectancy and must be modeled, not assumed zero.
- **Stop fill:** if a later bar's `low ≤ stop` → exit at `stop`; if the bar **opens
  below** stop (gap-through) → exit at that bar's **open** (worse fill).
- **Trail:** chandelier recomputed each bar on highest-high-since-entry.
- **Force-flat:** exit at the **15:55 ET** bar close if still open.
- **No look-ahead:** a bar's signal uses only data through that bar's close;
  fills happen on that bar or later.
- **Commissions:** Schwab equities $0.

Output per simulated trade (`Trade`): symbol, date, setup, entry, stop, exit,
exit_reason, **R-multiple**, bars_held.

## Scoring, ranking & report

`metrics.py` — pure, unit-testable functions. Per setup, across all trades:

- **N** trades, **win%**, **avg_win(R)**, **avg_loss(R)**
- **expectancy(R/trade)** — primary ranking key
- **profit_factor** = gross_win / gross_loss
- **max_drawdown(R)**, **max_consecutive_losers**
- **trades/day** (feasibility vs the ~1-trade/day cash limit)

**Ranking:** sort by expectancy, then apply **sanity gates** — drop any setup with
`N < N_MIN` (default 30) or whose expectancy edge is within noise of zero given N.
A 0.9R expectancy on 4 trades is explicitly flagged as not actionable.

**Report:** console table + a per-trade CSV (`state/backtest_out/<runid>.csv`) for
inspection. Echo realized sample size, dropped-setup notes, and bias caveats.

Optional secondary **account-sim**: apply a risk-% per trade (whole shares, $200
BP cap) to show equity-curve drawdown in dollars. Ranking stays in R-space;
account-sim is illustrative.

## Component / file layout

Each unit has one purpose and a clean interface:

- `scripts/research/gapper_universe.py` — `reconstruct(start, end, params) ->
  dict[date, list[str]]` from daily bars. Depends on `SchwabClient`.
- `scripts/research/indicators_ctx.py` — `build_context(day_bars) -> Ctx`
  (OR, VWAP, EMA9, ATR, PM-high, rel-vol). Pure over a DataFrame.
- `scripts/research/setups/` — `base.py` (interface) + `orb_clean.py`,
  `vwap_reclaim.py`, `first_pullback.py`, `pm_high_break.py`. Each:
  `evaluate(day_bars, ctx) -> Optional[Trade]`.
- `scripts/research/sim.py` — shared fill/exit/trail engine used by setups.
- `scripts/research/metrics.py` — pure scoring functions.
- `scripts/research/run_harness.py` — orchestrator: reconstruct universe → fetch/
  cache bars → run setups → score → rank → report. CLI flags for all params.
- `SchwabClient.get_history(...)` — new date-ranged bar fetch (daily + 5-min,
  optional extended hours).

`scripts/backtest_orb.py` stays as-is (ORB-specific); the harness generalizes it.

## Path to live (Phase 2 — out of scope for this spec)

1. Pick the leader from the report.
2. Wire it in as the new `Signal`-emitting strategy, replacing the ORB rule stack.
3. **dry_run forward-test** to accumulate out-of-sample days.
4. Review expectancy after N more live-dry days; go live only if the edge holds.

This spec covers Phase 1: the **universe reconstructor + expectancy harness**.

## Testing strategy

TDD throughout, using **synthetic 5-min bar fixtures** (no live API in unit tests):

- `gapper_universe`: a symbol with a 25% gap and enough $-vol qualifies; one below
  each threshold is excluded; top-N ranking by gap%.
- `indicators_ctx`: VWAP/EMA9/ATR/OR/PM-high computed correctly on a known frame.
- Each **setup**: a hand-built day where the setup should trigger (assert entry,
  stop, exit, R) and one where it should not (assert None).
- `sim`: stop hit intrabar fills at stop; gap-through fills at open; chandelier
  trail ratchets; 15:55 force-flat.
- `metrics`: expectancy / profit_factor / max_drawdown against hand-computed values.
- Data layer: `get_history` date args mapped correctly (mock schwab-py client).

## Open risks

- **Sample depth** is the chief threat to trustworthiness — bounded by Schwab's
  5-min lookback. Mitigation: report realized N; sanity-gate on N; forward-test
  before live.
- **Survivorship/selection bias** in universe reconstruction (documented above).
- **Setup D** contingent on extended-hours data availability.
- **Slippage assumption** (15 bps) is a guess for wide-spread names; expose it as a
  CLI param and show expectancy sensitivity to it.

## Key parameters (defaults, all CLI-configurable)

| Param | Default | Meaning |
|---|---|---|
| `GAP_MIN` | 0.20 | min gap-up to qualify |
| `DOLLAR_VOL_MIN` | $3M | liquidity floor |
| `PRICE_MIN` / `PRICE_MAX` | $1 / $20 | price band |
| `TOP_N` | 5 | gappers/day |
| `SLIP_BPS` | 15 | entry+exit slippage haircut |
| `ATR_PERIOD` / `CHANDELIER_K` | 14 / 3.0 | trail params |
| `N_MIN` | 30 | min trades for an actionable ranking |
