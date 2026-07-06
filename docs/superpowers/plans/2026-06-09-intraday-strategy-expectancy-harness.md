# Intraday Strategy Expectancy Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a research harness that reconstructs the historical gapper universe from price data, simulates four intraday setups on it, and ranks them by expectancy so the numbers pick the strategy.

**Architecture:** Pure, unit-testable units composed by one orchestrator. Data layer (`SchwabClient.get_history`) feeds a universe reconstructor and an indicator-context builder; a shared bias-controlled simulation engine and four setups produce `Trade`s; a metrics module scores and ranks them. All research code lives under `scripts/research/`.

**Tech Stack:** Python 3.12, pandas, pytest, schwab-py (mocked in tests).

**Shared signatures (held constant across tasks):**
- `SchwabClient.get_history(symbol, timeframe, start, end, extended_hours=False) -> pd.DataFrame` — cols `open/high/low/close/volume`, tz-aware UTC `DatetimeIndex`.
- `Trade` dataclass: `symbol, date, setup, entry, stop, exit, exit_reason, r_multiple, bars_held`.
- `make_trade(symbol, date, setup, entry, stop, exit_price, reason, bars_held, slip_bps=15.0) -> Trade`.
- `simulate_exit(bars_after, entry_price, initial_stop, k=3.0) -> tuple[float, str, int]` — `bars_after` has cols `open/high/low/close/atr`.
- `build_context(day_bars) -> Ctx` where `Ctx.bars` is the regular-session frame plus cols `et_time, vwap, ema9, atr`.
- `Setup.evaluate(ctx, slip_bps) -> Optional[Trade]`.
- `reconstruct(client, start, end, params) -> dict[str, list[str]]` keyed by ISO date.

---

### Task 1: `SchwabClient.get_history` — date-ranged bar fetch

**Files:**
- Modify: `src/core/schwab_client.py` (add method near `get_bars`, line ~207)
- Test: `tests/unit/test_schwab_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_get_history_passes_dates_and_normalizes(schwab, mock_schwab_py_client):
    from datetime import datetime
    candles = [
        {"datetime": 1715170200000, "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4, "volume": 1000},
        {"datetime": 1715170500000, "open": 10.4, "high": 10.7, "low": 10.3, "close": 10.6, "volume": 1500},
    ]
    mock_schwab_py_client.get_price_history_every_five_minutes.return_value = MagicMock(
        status_code=200, json=lambda: {"candles": candles, "empty": False},
    )
    start = datetime(2026, 5, 8); end = datetime(2026, 5, 9)
    df = schwab.get_history("AAPL", "5Min", start, end, extended_hours=True)

    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[-1] == 10.6
    _, kwargs = mock_schwab_py_client.get_price_history_every_five_minutes.call_args
    assert kwargs["start_datetime"] == start
    assert kwargs["end_datetime"] == end
    assert kwargs["need_extended_hours_data"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_schwab_client.py::test_get_history_passes_dates_and_normalizes -v`
Expected: FAIL — `AttributeError: 'SchwabClient' object has no attribute 'get_history'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/core/schwab_client.py`:

```python
    def get_history(self, symbol, timeframe="5Min", start=None, end=None,
                    extended_hours=False):
        """Date-ranged price history (daily or intraday) for backtests.

        Unlike get_bars (which fetches the API default window and tails it),
        this passes explicit start/end datetimes and optional pre/post-market
        bars. Returns the same normalized OHLCV frame.
        """
        if not self.is_authenticated:
            raise RuntimeError("SchwabClient not authenticated")
        method_name = self._TIMEFRAME_TO_METHOD.get(timeframe)
        if not method_name:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        method = getattr(self._client, method_name)
        resp = method(symbol, start_datetime=start, end_datetime=end,
                      need_extended_hours_data=extended_hours)
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"pricehistory failed: {resp.status_code}")
        candles = resp.json().get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_schwab_client.py::test_get_history_passes_dates_and_normalizes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/schwab_client.py tests/unit/test_schwab_client.py
git commit -m "schwab_client: add date-ranged get_history for backtests"
```

---

### Task 2: `metrics.py` — pure scoring functions

**Files:**
- Create: `scripts/research/__init__.py` (empty)
- Create: `scripts/research/metrics.py`
- Test: `tests/unit/research/test_metrics.py` (also create `tests/unit/research/__init__.py`)

- [ ] **Step 1: Write the failing test**

```python
from scripts.research.metrics import expectancy, profit_factor, max_drawdown_r, summarize

def _rs(*vals):  # list of r-multiples
    return list(vals)

def test_expectancy_basic():
    # 2 wins +2R, 2 losses -1R -> mean = (2+2-1-1)/4 = 0.5
    assert expectancy(_rs(2, 2, -1, -1)) == 0.5

def test_profit_factor():
    # gross win 4, gross loss 2 -> 2.0
    assert profit_factor(_rs(2, 2, -1, -1)) == 2.0

def test_max_drawdown_r():
    # cum: 2,1,3,0 ... peak 3 then 0 -> dd 3? walk: +2,-1,+2,-3 -> equity 2,1,3,0 peak3 trough0 dd=3
    assert max_drawdown_r(_rs(2, -1, 2, -3)) == 3.0

def test_summarize_shape():
    s = summarize("orb_clean", _rs(2, -1, 2, -1))
    assert s["setup"] == "orb_clean"
    assert s["n"] == 4
    assert s["win_pct"] == 0.5
    assert round(s["expectancy"], 3) == 0.5
    assert s["profit_factor"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.research.metrics'`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/metrics.py`:

```python
"""Pure scoring functions over lists of trade R-multiples."""
from __future__ import annotations


def expectancy(rs: list[float]) -> float:
    return sum(rs) / len(rs) if rs else 0.0


def profit_factor(rs: list[float]) -> float:
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def max_drawdown_r(rs: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def max_consecutive_losers(rs: list[float]) -> int:
    run = best = 0
    for r in rs:
        run = run + 1 if r < 0 else 0
        best = max(best, run)
    return best


def summarize(setup: str, rs: list[float]) -> dict:
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    return {
        "setup": setup,
        "n": n,
        "win_pct": (len(wins) / n) if n else 0.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": expectancy(rs),
        "profit_factor": profit_factor(rs),
        "max_drawdown_r": max_drawdown_r(rs),
        "max_consec_losers": max_consecutive_losers(rs),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_metrics.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/__init__.py scripts/research/metrics.py tests/unit/research/
git commit -m "research: add pure expectancy/scoring metrics"
```

---

### Task 3: `sim.py` — `Trade`, `make_trade`, `simulate_exit`

**Files:**
- Create: `scripts/research/sim.py`
- Test: `tests/unit/research/test_sim.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd
from scripts.research.sim import Trade, make_trade, simulate_exit

def _bars(rows):  # rows: list of (open, high, low, close, atr)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "atr"])

def test_make_trade_applies_slippage_and_computes_r():
    # entry 10, stop 9 -> planned risk 1. exit 12.
    # slip 100 bps: entry fill 10.10, exit fill 11.88 -> pnl 1.78 -> r 1.78
    t = make_trade("X", "2026-06-09", "orb_clean", 10.0, 9.0, 12.0, "trail", 5, slip_bps=100.0)
    assert isinstance(t, Trade)
    assert round(t.r_multiple, 2) == 1.78
    assert t.exit_reason == "trail"

def test_simulate_exit_stops_out_intrabar():
    # entry 10 stop 9.5; second bar low 9.4 -> exit at stop 9.5
    bars = _bars([(10.0, 10.2, 9.8, 10.1, 0.3), (10.0, 10.1, 9.4, 9.6, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 9.5 and reason == "stop" and held == 2

def test_simulate_exit_gap_through_fills_at_open():
    # bar opens 9.0 below stop 9.5 -> fill at open 9.0
    bars = _bars([(10.0, 10.2, 9.8, 10.1, 0.3), (9.0, 9.1, 8.8, 8.9, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 9.0 and reason == "gap_stop"

def test_simulate_exit_chandelier_trails_up():
    # price runs to 13 (atr 0.3, k=3 -> chandelier floor 13-0.9=12.1),
    # then a bar dips to 12.0 -> trail exit at 12.1
    bars = _bars([
        (10.0, 11.0, 9.9, 10.9, 0.3),
        (11.0, 13.0, 10.9, 12.9, 0.3),
        (12.9, 13.0, 12.0, 12.2, 0.3),
    ])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert round(px, 2) == 12.1 and reason == "trail"

def test_simulate_exit_force_flat_at_end():
    bars = _bars([(10.0, 10.5, 9.9, 10.4, 0.3), (10.4, 10.6, 10.2, 10.5, 0.3)])
    px, reason, held = simulate_exit(bars, entry_price=10.0, initial_stop=9.5, k=3.0)
    assert px == 10.5 and reason == "eod" and held == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.research.sim'`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/sim.py`:

```python
"""Bias-controlled intraday exit simulation and trade construction."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Trade:
    symbol: str
    date: str
    setup: str
    entry: float
    stop: float
    exit: float
    exit_reason: str
    r_multiple: float
    bars_held: int


def make_trade(symbol, date, setup, entry, stop, exit_price, reason, bars_held,
               slip_bps=15.0):
    """Apply slippage to entry (buy higher) and exit (sell lower) and compute R
    against the *planned* risk (entry - stop)."""
    slip = slip_bps / 10_000.0
    entry_fill = entry * (1 + slip)
    exit_fill = exit_price * (1 - slip)
    risk = entry - stop
    r = (exit_fill - entry_fill) / risk if risk > 0 else 0.0
    return Trade(symbol, date, setup, round(entry_fill, 4), round(stop, 4),
                 round(exit_fill, 4), reason, r, bars_held)


def simulate_exit(bars_after, entry_price, initial_stop, k=3.0):
    """Walk bars after entry; return (exit_price, reason, bars_held).

    Long-only. Each bar: ratchet a chandelier floor = highest_high - k*atr (never
    below initial_stop, never decreasing). Gap-through (open <= stop) fills at the
    bar open; intrabar (low <= stop) fills at the stop; otherwise force-flat at the
    last bar's close.
    """
    stop = initial_stop
    highest_high = entry_price
    held = 0
    n = len(bars_after)
    for i in range(n):
        row = bars_after.iloc[i]
        held = i + 1
        if row["open"] <= stop:
            return float(row["open"]), "gap_stop", held
        if row["low"] <= stop:
            return float(stop), "stop", held
        highest_high = max(highest_high, float(row["high"]))
        chandelier = highest_high - k * float(row["atr"])
        stop = max(stop, chandelier)
        if i == n - 1:
            return float(row["close"]), "eod", held
    return float(entry_price), "eod", held
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_sim.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/sim.py tests/unit/research/test_sim.py
git commit -m "research: add Trade, slippage-aware make_trade, simulate_exit engine"
```

---

### Task 4: `indicators_ctx.py` — `build_context`

**Files:**
- Create: `scripts/research/indicators_ctx.py`
- Test: `tests/unit/research/test_indicators_ctx.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd
from scripts.research.indicators_ctx import build_context

def _day():
    # 09:25 PM bar, then 09:30..10:10 session 5-min bars (ET), UTC index (+4 in EDT)
    times = ["13:25", "13:30", "13:35", "13:40", "13:45", "13:50", "13:55", "14:00"]
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in times])
    data = {
        "open":   [8.0, 9.0, 9.4, 9.2, 9.6, 9.5, 9.8, 9.7],
        "high":   [8.2, 9.5, 9.6, 9.5, 9.9, 9.7, 10.0, 9.9],
        "low":    [7.9, 8.9, 9.1, 9.0, 9.3, 9.4, 9.6, 9.5],
        "close":  [8.1, 9.4, 9.2, 9.4, 9.7, 9.6, 9.9, 9.8],
        "volume": [500, 4000, 3000, 2000, 1500, 1200, 1100, 1000],
    }
    return pd.DataFrame(data, index=idx)

def test_build_context_or_pm_and_columns():
    ctx = build_context(_day())
    # OR window 09:30-09:45 ET = first three session bars (13:30,13:35,13:40 UTC)
    assert ctx.or_high == 9.6
    assert ctx.or_low == 8.9
    assert ctx.or_volume == 9000
    # PM high from the 09:25 ET bar
    assert ctx.pm_high == 8.2
    # session frame excludes PM and carries indicator columns
    assert len(ctx.bars) == 7
    for col in ("et_time", "vwap", "ema9", "atr"):
        assert col in ctx.bars.columns
    # vwap of first session bar = typical price (h+l+c)/3
    first = ctx.bars.iloc[0]
    assert round(first["vwap"], 4) == round((9.5 + 8.9 + 9.4) / 3, 4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_indicators_ctx.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/indicators_ctx.py`:

```python
"""Per-day intraday indicator context for the setups."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional

import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
SESSION_OPEN = time(9, 30)
OR_END = time(9, 45)
SESSION_CLOSE = time(16, 0)


@dataclass
class Ctx:
    bars: pd.DataFrame          # session bars + cols: et_time, vwap, ema9, atr
    or_high: float
    or_low: float
    or_volume: float
    pm_high: Optional[float]


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def build_context(day_bars: pd.DataFrame) -> Ctx:
    et = day_bars.index.tz_convert(ET)
    et_time = pd.Series([t.time() for t in et], index=day_bars.index)

    pm_mask = et_time < SESSION_OPEN
    pm_high = float(day_bars.loc[pm_mask, "high"].max()) if pm_mask.any() else None

    sess = day_bars.loc[(et_time >= SESSION_OPEN) & (et_time <= SESSION_CLOSE)].copy()
    sess["et_time"] = et_time[sess.index].values

    or_mask = (sess["et_time"] >= SESSION_OPEN) & (sess["et_time"] < OR_END)
    or_bars = sess.loc[or_mask]
    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_volume = float(or_bars["volume"].sum())

    typical = (sess["high"] + sess["low"] + sess["close"]) / 3
    sess["vwap"] = (typical * sess["volume"]).cumsum() / sess["volume"].cumsum()
    sess["ema9"] = sess["close"].ewm(span=9, adjust=False).mean()
    sess["atr"] = _atr(sess)

    return Ctx(bars=sess, or_high=or_high, or_low=or_low,
               or_volume=or_volume, pm_high=pm_high)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_indicators_ctx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/research/indicators_ctx.py tests/unit/research/test_indicators_ctx.py
git commit -m "research: add per-day indicator context (OR, VWAP, EMA9, ATR, PM-high)"
```

---

### Task 5: `setups/base.py` + Setup A (ORB-clean)

**Files:**
- Create: `scripts/research/setups/__init__.py` (empty)
- Create: `scripts/research/setups/base.py`
- Create: `scripts/research/setups/orb_clean.py`
- Test: `tests/unit/research/test_setup_orb_clean.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.research.indicators_ctx import build_context
from scripts.research.setups.orb_clean import ORBClean
from tests.unit.research.fixtures import make_day  # helper added below

def test_orb_clean_enters_on_close_above_or_high():
    # OR high ~9.6 from first 3 session bars; bar 4 closes above at 9.9 -> entry
    day = make_day(session_closes=[9.4, 9.2, 9.4, 9.9, 10.5, 10.4],
                   session_highs=[9.5, 9.3, 9.6, 10.0, 10.6, 10.5],
                   session_lows=[8.9, 9.0, 9.1, 9.5, 10.0, 10.0])
    ctx = build_context(day)
    trade = ORBClean().evaluate(ctx, slip_bps=0.0)
    assert trade is not None
    assert trade.setup == "orb_clean"
    # stop = breakout-bar low (9.5), entry = breakout close (9.9)
    assert trade.stop == 9.5

def test_orb_clean_no_entry_when_never_breaks():
    day = make_day(session_closes=[9.4, 9.2, 9.3, 9.4, 9.3, 9.2],
                   session_highs=[9.5, 9.3, 9.6, 9.5, 9.4, 9.3],
                   session_lows=[8.9, 9.0, 9.1, 9.0, 9.0, 8.9])
    ctx = build_context(day)
    assert ORBClean().evaluate(ctx, slip_bps=0.0) is None
```

Also create `tests/unit/research/fixtures.py`:

```python
import pandas as pd

def make_day(session_closes, session_highs, session_lows,
             session_opens=None, volumes=None, pm=True):
    """Build a UTC-indexed 5-min day. First bar is a 09:25 ET pre-market bar
    (if pm), followed by 09:30+ session bars matching the given lists."""
    n = len(session_closes)
    opens = session_opens or [c for c in session_closes]
    vols = volumes or [2000] * n
    rows_t, o, h, l, c, v = [], [], [], [], [], []
    if pm:
        rows_t.append("13:25"); o.append(8.0); h.append(8.2); l.append(7.9); c.append(8.1); v.append(500)
    for i in range(n):
        mins = 30 + i * 5
        rows_t.append(f"13:{mins:02d}" if mins < 60 else f"14:{mins-60:02d}")
        o.append(opens[i]); h.append(session_highs[i]); l.append(session_lows[i])
        c.append(session_closes[i]); v.append(vols[i])
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in rows_t])
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_setup_orb_clean.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.research.setups.orb_clean'`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/setups/base.py`:

```python
"""Common setup interface."""
from __future__ import annotations

from typing import Optional

from scripts.research.indicators_ctx import Ctx
from scripts.research.sim import Trade, make_trade, simulate_exit


class Setup:
    key = "base"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        raise NotImplementedError

    @staticmethod
    def _exit_from(ctx: Ctx, entry_idx: int, entry: float, stop: float,
                   key: str, slip_bps: float) -> Trade:
        bars_after = ctx.bars.iloc[entry_idx + 1:][["open", "high", "low", "close", "atr"]]
        exit_px, reason, held = simulate_exit(bars_after, entry, stop)
        date = ctx.bars.index[0].date().isoformat()
        symbol = getattr(ctx, "symbol", "?")
        return make_trade(symbol, date, key, entry, stop, exit_px, reason, held, slip_bps)
```

`scripts/research/setups/orb_clean.py`:

```python
"""Setup A: first 5-min close above the opening-range high; stop = breakout bar low."""
from __future__ import annotations

from datetime import time
from typing import Optional

from scripts.research.indicators_ctx import Ctx, OR_END
from scripts.research.setups.base import Setup
from scripts.research.sim import Trade

EOD_CUTOFF = time(15, 55)


class ORBClean(Setup):
    key = "orb_clean"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        bars = ctx.bars
        for i in range(len(bars)):
            row = bars.iloc[i]
            if row["et_time"] < OR_END or row["et_time"] >= EOD_CUTOFF:
                continue
            if row["close"] > ctx.or_high:
                entry = float(row["close"])
                stop = float(row["low"])
                if stop >= entry:
                    return None
                return self._exit_from(ctx, i, entry, stop, self.key, slip_bps)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_setup_orb_clean.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/setups/ tests/unit/research/test_setup_orb_clean.py tests/unit/research/fixtures.py
git commit -m "research: add setup interface + ORB-clean (A)"
```

---

### Task 6: Setup B (VWAP reclaim)

**Files:**
- Create: `scripts/research/setups/vwap_reclaim.py`
- Test: `tests/unit/research/test_setup_vwap_reclaim.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.research.indicators_ctx import build_context
from scripts.research.setups.vwap_reclaim import VWAPReclaim
from tests.unit.research.fixtures import make_day

def test_vwap_reclaim_enters_on_reclaim_and_hold():
    # price dips below vwap then a bar closes back above vwap with low >= prior low
    day = make_day(session_closes=[10.0, 9.5, 9.4, 9.9, 10.2, 10.3],
                   session_highs=[10.1, 9.7, 9.6, 10.0, 10.3, 10.4],
                   session_lows=[9.8, 9.3, 9.2, 9.5, 10.0, 10.1])
    ctx = build_context(day)
    trade = VWAPReclaim().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "vwap_reclaim"

def test_vwap_reclaim_no_entry_when_always_below():
    day = make_day(session_closes=[10.0, 9.5, 9.3, 9.2, 9.1, 9.0],
                   session_highs=[10.1, 9.7, 9.5, 9.4, 9.3, 9.2],
                   session_lows=[9.8, 9.3, 9.1, 9.0, 8.9, 8.8])
    ctx = build_context(day)
    assert VWAPReclaim().evaluate(ctx, slip_bps=0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_setup_vwap_reclaim.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/setups/vwap_reclaim.py`:

```python
"""Setup B: a 5-min close reclaims VWAP (prev close below, this close above) while
holding (low >= prior bar low). Stop = the reclaim bar low."""
from __future__ import annotations

from datetime import time
from typing import Optional

from scripts.research.indicators_ctx import Ctx, SESSION_OPEN
from scripts.research.setups.base import Setup
from scripts.research.sim import Trade

EOD_CUTOFF = time(15, 55)


class VWAPReclaim(Setup):
    key = "vwap_reclaim"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        bars = ctx.bars
        for i in range(1, len(bars)):
            row = bars.iloc[i]
            prev = bars.iloc[i - 1]
            if row["et_time"] < SESSION_OPEN or row["et_time"] >= EOD_CUTOFF:
                continue
            reclaimed = prev["close"] < prev["vwap"] and row["close"] > row["vwap"]
            holding = row["low"] >= prev["low"]
            if reclaimed and holding:
                entry = float(row["close"])
                stop = float(row["low"])
                if stop >= entry:
                    return None
                return self._exit_from(ctx, i, entry, stop, self.key, slip_bps)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_setup_vwap_reclaim.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/setups/vwap_reclaim.py tests/unit/research/test_setup_vwap_reclaim.py
git commit -m "research: add VWAP-reclaim setup (B)"
```

---

### Task 7: Setup C (first-pullback continuation)

**Files:**
- Create: `scripts/research/setups/first_pullback.py`
- Test: `tests/unit/research/test_setup_first_pullback.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.research.indicators_ctx import build_context
from scripts.research.setups.first_pullback import FirstPullback
from tests.unit.research.fixtures import make_day

def test_first_pullback_enters_after_drive_and_higher_low():
    # opening drive up, then a pullback whose low holds above the prior pullback,
    # then a bar reclaims back above the 9-EMA -> entry
    day = make_day(session_closes=[9.6, 10.4, 10.2, 10.1, 10.6, 10.9],
                   session_highs=[9.7, 10.5, 10.3, 10.2, 10.7, 11.0],
                   session_lows=[9.3, 10.0, 10.0, 10.05, 10.3, 10.6])
    ctx = build_context(day)
    trade = FirstPullback().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "first_pullback"

def test_first_pullback_no_entry_without_drive():
    day = make_day(session_closes=[9.4, 9.3, 9.35, 9.3, 9.32, 9.31],
                   session_highs=[9.5, 9.4, 9.45, 9.4, 9.42, 9.41],
                   session_lows=[9.2, 9.2, 9.25, 9.2, 9.22, 9.21])
    ctx = build_context(day)
    assert FirstPullback().evaluate(ctx, slip_bps=0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_setup_first_pullback.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/setups/first_pullback.py`:

```python
"""Setup C: after an opening drive of >= 1 ATR off the open, take the first bar
that pulls back toward the 9-EMA and then closes back above it. Stop = pullback low."""
from __future__ import annotations

from datetime import time
from typing import Optional

from scripts.research.indicators_ctx import Ctx, SESSION_OPEN
from scripts.research.setups.base import Setup
from scripts.research.sim import Trade

EOD_CUTOFF = time(15, 55)


class FirstPullback(Setup):
    key = "first_pullback"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        bars = ctx.bars
        if len(bars) < 2:
            return None
        session_open = float(bars.iloc[0]["open"])
        drive_seen = False
        pulled_back = False
        for i in range(1, len(bars)):
            row = bars.iloc[i]
            if row["et_time"] < SESSION_OPEN or row["et_time"] >= EOD_CUTOFF:
                continue
            atr = float(row["atr"]) or 0.0
            if not drive_seen:
                if float(row["high"]) - session_open >= atr and atr > 0:
                    drive_seen = True
                continue
            # after a drive, look for a touch of the 9-EMA (pullback)...
            if not pulled_back:
                if float(row["low"]) <= float(row["ema9"]):
                    pulled_back = True
                continue
            # ...then a reclaim close above the 9-EMA
            if float(row["close"]) > float(row["ema9"]):
                entry = float(row["close"])
                stop = float(bars.iloc[i - 1]["low"])
                if stop >= entry:
                    return None
                return self._exit_from(ctx, i, entry, stop, self.key, slip_bps)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_setup_first_pullback.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/setups/first_pullback.py tests/unit/research/test_setup_first_pullback.py
git commit -m "research: add first-pullback continuation setup (C)"
```

---

### Task 8: Setup D (premarket-high break)

**Files:**
- Create: `scripts/research/setups/pm_high_break.py`
- Test: `tests/unit/research/test_setup_pm_high_break.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.research.indicators_ctx import build_context
from scripts.research.setups.pm_high_break import PMHighBreak
from tests.unit.research.fixtures import make_day

def test_pm_high_break_enters_on_close_above_pm_high():
    # fixture PM bar high = 8.2; a session bar closes above it -> entry
    day = make_day(session_closes=[7.9, 8.0, 8.3, 8.6, 8.5, 8.7],
                   session_highs=[8.0, 8.1, 8.4, 8.7, 8.6, 8.8],
                   session_lows=[7.7, 7.9, 8.0, 8.3, 8.3, 8.4])
    ctx = build_context(day)
    trade = PMHighBreak().evaluate(ctx, slip_bps=0.0)
    assert trade is not None and trade.setup == "pm_high_break"

def test_pm_high_break_returns_none_without_pm_data():
    day = make_day(session_closes=[8.3, 8.4, 8.5, 8.6, 8.5, 8.7],
                   session_highs=[8.4, 8.5, 8.6, 8.7, 8.6, 8.8],
                   session_lows=[8.0, 8.1, 8.2, 8.3, 8.3, 8.4], pm=False)
    ctx = build_context(day)
    assert PMHighBreak().evaluate(ctx, slip_bps=0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_setup_pm_high_break.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/setups/pm_high_break.py`:

```python
"""Setup D: first 5-min close above the pre-market high. Stop = last swing low
(prior bar low). Returns None when no pre-market data is available."""
from __future__ import annotations

from datetime import time
from typing import Optional

from scripts.research.indicators_ctx import Ctx, SESSION_OPEN
from scripts.research.setups.base import Setup
from scripts.research.sim import Trade

EOD_CUTOFF = time(15, 55)


class PMHighBreak(Setup):
    key = "pm_high_break"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        if ctx.pm_high is None:
            return None
        bars = ctx.bars
        for i in range(1, len(bars)):
            row = bars.iloc[i]
            if row["et_time"] < SESSION_OPEN or row["et_time"] >= EOD_CUTOFF:
                continue
            if row["close"] > ctx.pm_high:
                entry = float(row["close"])
                stop = float(bars.iloc[i - 1]["low"])
                if stop >= entry:
                    return None
                return self._exit_from(ctx, i, entry, stop, self.key, slip_bps)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_setup_pm_high_break.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/setups/pm_high_break.py tests/unit/research/test_setup_pm_high_break.py
git commit -m "research: add premarket-high-break setup (D)"
```

---

### Task 9: `gapper_universe.py` — reconstruct the historical universe

**Files:**
- Create: `scripts/research/gapper_universe.py`
- Test: `tests/unit/research/test_gapper_universe.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd
from scripts.research.gapper_universe import qualifies, rank_day, DEFAULT_PARAMS

def _daily(prev_close, open_, close, volume):
    return {"prev_close": prev_close, "open": open_, "close": close, "volume": volume}

def test_qualifies_true_for_clean_gapper():
    # 25% gap, $5 price, $4M dollar-vol
    assert qualifies(_daily(4.0, 5.0, 5.2, 800_000), DEFAULT_PARAMS) is True

def test_qualifies_false_small_gap():
    assert qualifies(_daily(4.9, 5.0, 5.1, 800_000), DEFAULT_PARAMS) is False

def test_qualifies_false_too_expensive():
    assert qualifies(_daily(20.0, 25.0, 25.0, 800_000), DEFAULT_PARAMS) is False

def test_qualifies_false_thin_volume():
    assert qualifies(_daily(4.0, 5.0, 5.0, 1000), DEFAULT_PARAMS) is False

def test_rank_day_takes_top_n_by_gap():
    rows = {
        "AAA": _daily(4.0, 5.0, 5.0, 800_000),    # +25%
        "BBB": _daily(2.0, 3.0, 3.0, 2_000_000),  # +50%
        "CCC": _daily(4.9, 5.0, 5.0, 800_000),    # +2% (excluded)
    }
    out = rank_day(rows, {**DEFAULT_PARAMS, "top_n": 1})
    assert out == ["BBB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_gapper_universe.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/gapper_universe.py`:

```python
"""Reconstruct the historical gapper universe from daily OHLCV.

A symbol qualifies on day D if its open gaps up >= GAP_MIN over the prior close,
trades >= DOLLAR_VOL_MIN, and opens inside the price band. rank_day returns the
top-N qualifiers by gap%, mirroring the live top-5 selector. reconstruct() drives
this across a date range via SchwabClient.get_history daily bars.
"""
from __future__ import annotations

from datetime import date, timedelta

DEFAULT_PARAMS = {
    "gap_min": 0.20,
    "dollar_vol_min": 3_000_000.0,
    "price_min": 1.0,
    "price_max": 20.0,
    "top_n": 5,
}


def _gap_pct(row: dict) -> float:
    return row["open"] / row["prev_close"] - 1.0 if row["prev_close"] else 0.0


def qualifies(row: dict, params: dict) -> bool:
    if row["prev_close"] <= 0:
        return False
    if _gap_pct(row) < params["gap_min"]:
        return False
    if not (params["price_min"] <= row["open"] <= params["price_max"]):
        return False
    if row["close"] * row["volume"] < params["dollar_vol_min"]:
        return False
    return True


def rank_day(rows: dict, params: dict) -> list[str]:
    qualified = [(s, _gap_pct(r)) for s, r in rows.items() if qualifies(r, params)]
    qualified.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in qualified[: params["top_n"]]]


def reconstruct(client, symbols, start: date, end: date, params=None) -> dict:
    """Return {iso_date: [symbols]} of reconstructed gappers per trading day.

    Fetches daily bars per symbol once over [start-5d, end], then for each date
    builds the per-symbol {prev_close, open, close, volume} rows and ranks them.
    """
    params = params or DEFAULT_PARAMS
    fetch_start = start - timedelta(days=5)
    daily = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", fetch_start, end)
        if not df.empty:
            daily[sym] = df

    by_date: dict[str, dict] = {}
    for sym, df in daily.items():
        closes = df["close"].tolist()
        opens = df["open"].tolist()
        vols = df["volume"].tolist()
        dates = [ts.date() for ts in df.index]
        for i in range(1, len(df)):
            d = dates[i]
            if d < start or d > end:
                continue
            by_date.setdefault(d.isoformat(), {})[sym] = {
                "prev_close": closes[i - 1],
                "open": opens[i],
                "close": closes[i],
                "volume": vols[i],
            }

    return {d: rank_day(rows, params) for d, rows in sorted(by_date.items())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_gapper_universe.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/research/gapper_universe.py tests/unit/research/test_gapper_universe.py
git commit -m "research: add gapper-universe reconstruction from daily bars"
```

---

### Task 10: `run_harness.py` — orchestrator, caching, CLI + report

**Files:**
- Create: `scripts/research/run_harness.py`
- Test: `tests/unit/research/test_run_harness.py`

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd
from scripts.research.run_harness import run_setups_on_day, ALL_SETUPS

def _day():
    times = ["13:25", "13:30", "13:35", "13:40", "13:45", "13:50", "13:55", "14:00"]
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in times])
    return pd.DataFrame({
        "open":   [8.0, 9.0, 9.4, 9.2, 9.9, 10.2, 10.4, 10.3],
        "high":   [8.2, 9.5, 9.6, 9.5, 10.0, 10.3, 10.5, 10.4],
        "low":    [7.9, 8.9, 9.1, 9.0, 9.6, 10.0, 10.1, 10.0],
        "close":  [8.1, 9.4, 9.2, 9.4, 9.9, 10.2, 10.4, 10.3],
        "volume": [500, 4000, 3000, 2000, 1500, 1200, 1100, 1000],
    }, index=idx)

def test_run_setups_on_day_returns_trades_keyed_by_setup():
    out = run_setups_on_day("AAA", _day(), slip_bps=0.0)
    assert set(out.keys()) == set(s.key for s in ALL_SETUPS)
    # ORB-clean should have produced a trade on this breakout day
    assert out["orb_clean"] is not None
    assert out["orb_clean"].symbol == "AAA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/research/test_run_harness.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`scripts/research/run_harness.py`:

```python
"""Orchestrate the expectancy harness: reconstruct universe -> fetch/cache 5-min
bars -> run setups -> score -> rank -> report.

CLI:
  python -m scripts.research.run_harness --start 2026-03-01 --end 2026-06-01 \
      --symbols-file scripts/research/scan_symbols.txt [--slip-bps 15] [--gap-min 0.20]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from scripts.research.gapper_universe import DEFAULT_PARAMS, reconstruct
from scripts.research.indicators_ctx import build_context
from scripts.research.metrics import summarize
from scripts.research.setups.first_pullback import FirstPullback
from scripts.research.setups.orb_clean import ORBClean
from scripts.research.setups.pm_high_break import PMHighBreak
from scripts.research.setups.vwap_reclaim import VWAPReclaim

ALL_SETUPS = [ORBClean(), VWAPReclaim(), FirstPullback(), PMHighBreak()]
CACHE_DIR = Path("state/backtest_cache")


def run_setups_on_day(symbol: str, day_bars: pd.DataFrame, slip_bps: float) -> dict:
    """Run every setup on one symbol-day. Returns {setup_key: Trade|None}."""
    ctx = build_context(day_bars)
    ctx.symbol = symbol  # consumed by Setup._exit_from
    out = {}
    for setup in ALL_SETUPS:
        try:
            out[setup.key] = setup.evaluate(ctx, slip_bps)
        except Exception:
            out[setup.key] = None
    return out


def _cached_5min(client, symbol: str, day: date, extended: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fp = CACHE_DIR / symbol / f"{day.isoformat()}.parquet"
    if fp.exists():
        return pd.read_parquet(fp)
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    df = client.get_history(symbol, "5Min", start, end, extended_hours=extended)
    if not df.empty:
        fp.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(fp)
    return df


def run(client, symbols, start: date, end: date, params, slip_bps: float) -> list[dict]:
    universe = reconstruct(client, symbols, start, end, params)
    trades_by_setup: dict[str, list[float]] = {s.key: [] for s in ALL_SETUPS}
    n_days = 0
    for iso_day, syms in universe.items():
        d = date.fromisoformat(iso_day)
        for sym in syms:
            bars = _cached_5min(client, sym, d, extended=True)
            if bars.empty:
                continue
            n_days += 1
            for key, trade in run_setups_on_day(sym, bars, slip_bps).items():
                if trade is not None:
                    trades_by_setup[key].append(trade.r_multiple)
    reports = [summarize(k, rs) for k, rs in trades_by_setup.items()]
    reports.sort(key=lambda r: r["expectancy"], reverse=True)
    print(f"\nReconstructed setup-days evaluated: {n_days}\n")
    print(f"{'setup':<16}{'n':>5}{'win%':>7}{'avgW':>7}{'avgL':>7}"
          f"{'exp(R)':>8}{'PF':>6}{'maxDD':>7}")
    for r in reports:
        flag = "" if r["n"] >= params.get("n_min", 30) else "  (low-N)"
        print(f"{r['setup']:<16}{r['n']:>5}{r['win_pct']*100:>6.0f}%"
              f"{r['avg_win']:>7.2f}{r['avg_loss']:>7.2f}{r['expectancy']:>8.3f}"
              f"{r['profit_factor']:>6.2f}{r['max_drawdown_r']:>7.1f}{flag}")
    return reports


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--symbols-file", required=True)
    p.add_argument("--slip-bps", type=float, default=15.0)
    p.add_argument("--gap-min", type=float, default=DEFAULT_PARAMS["gap_min"])
    p.add_argument("--top-n", type=int, default=DEFAULT_PARAMS["top_n"])
    p.add_argument("--n-min", type=int, default=30)
    args = p.parse_args(argv)

    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text().split() if s.strip()]
    cfg = get_bot_config()
    client = SchwabClient(
        app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
        callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path,
    )
    params = {**DEFAULT_PARAMS, "gap_min": args.gap_min, "top_n": args.top_n,
              "n_min": args.n_min}
    run(client, symbols, date.fromisoformat(args.start), date.fromisoformat(args.end),
        params, args.slip_bps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/research/test_run_harness.py -v`
Expected: PASS

- [ ] **Step 5: Run the full research suite**

Run: `python -m pytest tests/unit/research/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/research/run_harness.py tests/unit/research/test_run_harness.py
git commit -m "research: add harness orchestrator, 5-min cache, ranked report + CLI"
```

---

### Task 11: Smoke-run on the server (manual validation)

**Files:** none (operational step)

- [ ] **Step 1:** Build a `scripts/research/scan_symbols.txt` seed list (start from recent `/sgt/api/scanner` symbols + a static small-cap list).
- [ ] **Step 2:** On the server (where `schwab-py` + token live), run:
  `python -m scripts.research.run_harness --start <~3mo ago> --end <yesterday> --symbols-file scripts/research/scan_symbols.txt`
- [ ] **Step 3:** Confirm the report prints, records realized setup-day count, and flags low-N setups. Capture the ranking.
- [ ] **Step 4:** Note actual Schwab 5-min lookback depth (the realized sample size) and whether pre-market bars came back (setup D viability). Record both in the run output / a short results note.

---

## Notes for the executor

- `scripts/` is not currently a package; tests import via `scripts.research.*`. Ensure `scripts/__init__.py` and `scripts/research/__init__.py` exist (the empty `scripts/__init__.py` may already be needed — create it in Task 2 if absent).
- The 8 pre-existing `schwab_client`/`schwab_stream` unit failures (schwab-py not installed locally) are unrelated; ignore them. The research suite mocks all I/O and runs clean locally.
- `pyarrow` (for parquet caching) may need installing in the server venv; if absent, swap `to_parquet`/`read_parquet` for `to_pickle`/`read_pickle`.
```
