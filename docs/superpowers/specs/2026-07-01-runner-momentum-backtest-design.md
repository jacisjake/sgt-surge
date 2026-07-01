# Runner-Momentum Backtest — Design (Sub-project 1)

**Goal:** Determine whether a HOD-break momentum-ignition strategy on intraday
small-cap runners has a positive, believable expectancy — *before* risking real
money. Produce one go/no-go number (expectancy per trade, R-distribution, equity
curve) from an Alpaca-minute-bar backtest that replays the live scanner's logic
on history.

This is Sub-project 1 of 2. Sub-project 2 (live gated trading) is built ONLY if
this backtest shows an edge, and gets its own spec.

---

## Non-goals
- No live trading, no order placement, no bot wiring. (That is Sub-project 2.)
- Not closing the swing survivorship gap — Alpaca has no delisted tickers.
- Not perfect fill modeling. Minute-bar fills on thin small-caps are optimistic;
  this backtest is a *filter*, not a promise. Live small-size stays the final judge.

---

## Success / kill criteria (set now, before we see results)
Params are fixed defaults mirroring the live scanner + common values — they are
NOT fit to this data, so there is no in-sample/out-of-sample split; the overfit
guard is (a) not tuning and (b) the slippage sweep as a fragility test.
- **Keep** if: expectancy > costs, positive across ≥100 simulated trades, t-stat
  > 2, edge survives the 50-bps slippage case, and no single day/name dominates P&L.
- **Kill** if: expectancy ≤ 0 after realistic slippage, or the edge only survives
  at 30 bps, or it depends on a handful of names.
- Explicitly **log what got dropped** (data gaps, skipped names) — no silent caps.

---

## Architecture

Four units, each independently testable:

### 1. `AlpacaDataClient` (`src/core/alpaca_client.py`)
Thin wrapper mirroring `SchwabClient.get_history`'s shape so downstream code is
data-source-agnostic.
- `get_daily_bars(symbol, start, end) -> DataFrame` (OHLCV, DatetimeIndex)
- `get_minute_bars(symbol, day) -> DataFrame` (1-min OHLCV for one session, ET)
- Reads `ALPACA_API_KEY` / `ALPACA_API_SECRET` from env. Free IEX feed by default;
  a `feed=` param allows SIP later without code changes.
- Returns empty DataFrame (not exception) on missing/failed data, so the harness
  skips-and-logs rather than crashing.

### 2. Runner-universe reconstruction (`scripts/research/runners/universe.py`)
Replays the live scanner on history via a **two-stage funnel** (respects free-tier
rate limits — daily bars are cheap, minute bars expensive):
- **Stage A (daily scan, cheap):** over a candidate symbol list, pull daily bars.
  For each (symbol, day), flag a *candidate runner day* when the day meets the
  live scanner thresholds computed from daily data: `close in [2.50, 10.00]`,
  `intraday_change_pct >= 10%` (from prior close), `dollar_volume >= 500_000`.
- **Stage B (minute pull, only for hits):** pull 1-min bars only for flagged
  (symbol, day) pairs → hand to the strategy sim.
- **Candidate list:** a static, versioned universe file of historically-active
  low-priced US small-caps (~500 names) at `scripts/research/runners/candidates.txt`.
  This is a KNOWN survivorship limitation (curated from names that exist today);
  documented, not hidden. Thresholds mirror `config.py` scanner values by import,
  not by copy, so backtest == live.

### 3. Strategy logic (`scripts/research/runners/runner_strategy.py`) — pure, TDD'd
Pure functions over a 1-min session DataFrame, no I/O:
- `track_hod(bars) -> series` — running high-of-day.
- `detect_coil(bars, i, n_bars, max_range_pct) -> bool` — last `n_bars` are a tight
  consolidation (high-low range contraction) below/at HOD.
- `entry_signal(bars, i, vol_mult) -> bool` — bar `i` closes above the coil high
  AND its volume >= `vol_mult` × coil average volume. One entry per symbol/day.
- `simulate_trade(bars, entry_i, ...) -> dict` — from entry: stop = coil low
  (R = entry − stop); **sell half at +1R**, move stop to breakeven; **chandelier
  trail** the remainder (`HH_since_entry − atr_mult × ATR`); **flatten by 15:55 ET**.
  Returns `{entry, exit_avg, r_multiple, return_pct, reason, ...}`.
- All returns fractional and after slippage (see below). Gap-down stop fills use
  the existing `stop_fill_price(stop, bar_open)` helper — same hardening as swing.

### 4. Backtest harness + report (`scripts/research/runners/backtest_runners.py`)
- For each candidate runner day → fetch minute bars → run strategy → collect trades.
- Aggregate: expectancy (mean R and mean return%), win rate, R-distribution,
  n trades, per-day P&L, worst day, equity curve on a $200 fractional account
  (reuse `simulate_portfolio`), and concentration check (top-3 names' share of P&L).
- Print a report + write trades to JSON for inspection.

---

## Strategy parameters (defaults; tunable)
| Param | Default | Meaning |
|---|---|---|
| price band | $2.50–$10.00 | from `config.py` scanner |
| min intraday change | +10% | from `config.py` scanner |
| min $ volume | $500K | from `config.py` scanner |
| coil bars (`n_bars`) | 3 | tight bars before break |
| coil max range | 3% | consolidation tightness |
| volume surge (`vol_mult`) | 2.0× | breakout bar vs coil avg vol |
| scale-out | 50% at +1R | bank half, stop→breakeven |
| chandelier `atr_mult` | 2.5 | trail on remainder |
| EOD flatten | 15:55 ET | no overnight |
| slippage | 30 bps/side | stressed to 50/100 bps |

---

## Slippage & data-quality (the honesty controls)
- **Slippage sweep:** run the whole backtest at 30 / 50 / 100 bps per side. If the
  edge only survives at 30, it's not real for thin runners. Report all three.
- **IEX data-quality gate:** before trusting any number, sample ~10 known runner
  days and check minute-bar completeness/coverage on the free IEX feed. If bars are
  sparse for small-caps, flag it loudly and treat results as directional only /
  recommend SIP upgrade. This check runs first and its verdict is in the report.

---

## Testing
- `AlpacaDataClient`: unit test the DataFrame shaping with a recorded/mocked
  response (no live calls in tests).
- Strategy pure functions: TDD — coil detection, entry trigger, scale-out+trail,
  gap-down stop, EOD flatten, dedup. Synthetic minute DataFrames, same style as
  the swing `test_strategies.py`.
- Universe reconstruction: unit test the daily-threshold flagging with synthetic
  daily bars.

---

## Prerequisites / config (operator actions)
- Create a **free Alpaca account** → generate API key + secret → add
  `ALPACA_API_KEY` / `ALPACA_API_SECRET` to `.env` (local for backtest; server
  later only if Sub-project 2 proceeds).
- `pip install alpaca-py` added to `requirements.txt`.

---

## Risks / caveats (carried forward, stated plainly)
1. **IEX-only free data** may be too thin for small-cap runners → data-quality gate.
2. **Candidate-list survivorship** — curated from currently-listed names; documented.
3. **Fill realism** — minute-bar fills flatter thin runners; slippage sweep + live
   small-size (Sub-project 2) are the mitigations.
4. **Overfit tax** — every tuned param is risk; defaults mirror the live scanner and
   common values, and the slippage sweep is the fragility check.
