"""Setup E: Sneaky Pivot — long-side mean-reversion off prior-day support.

Entry logic:
  1. Opening 15-min candle (c0) taps or breaches the prior-day low (prev_low).
  2. The next 15-min candle (c1) is green (close > open) — the "sneaky" reversal.
  3. Entry triggers on the first 5-min bar after c1 whose high crosses c1's high
     (sneaky_high), before the 15:55 ET cutoff.
  4. Stop = min(c0.low, c1.low); target = ctx.prev_high.
"""
from __future__ import annotations

from datetime import time
from typing import Optional

from scripts.research.indicators_ctx import Ctx
from scripts.research.setups.base import Setup
from scripts.research.sim import Trade

EOD_CUTOFF = time(15, 55)


class SneakyPivot(Setup):
    key = "sneaky_pivot"

    def evaluate(self, ctx: Ctx, slip_bps: float = 15.0) -> Optional[Trade]:
        if ctx.prev_low is None:
            return None

        bars = ctx.bars

        # --- aggregate into 15-min candles (groups of 3 consecutive session bars) ---
        n_bars = len(bars)
        candles = []
        i = 0
        while i + 2 < n_bars:
            b0 = bars.iloc[i]
            b1 = bars.iloc[i + 1]
            b2 = bars.iloc[i + 2]
            candle = {
                "open":     float(b0["open"]),
                "high":     max(float(b0["high"]), float(b1["high"]), float(b2["high"])),
                "low":      min(float(b0["low"]),  float(b1["low"]),  float(b2["low"])),
                "close":    float(b2["close"]),
                "last_idx": i + 2,  # index in ctx.bars of the last 5-min bar
            }
            candles.append(candle)
            i += 3

        if len(candles) < 3:
            return None

        c0 = candles[0]
        c1 = candles[1]

        # Condition: c0 tapped/pierced prev_low
        if c0["low"] > ctx.prev_low:
            return None

        # Condition: c1 is green
        if c1["close"] <= c1["open"]:
            return None

        sneaky_high = c1["high"]
        defended_low = min(c0["low"], c1["low"])
        target = ctx.prev_high  # may be None -> no fixed target

        if defended_low >= sneaky_high:
            return None

        # Scan 5-min bars after c1's last bar for the entry trigger
        scan_start = c1["last_idx"] + 1
        for j in range(scan_start, n_bars):
            row = bars.iloc[j]
            if row["et_time"] >= EOD_CUTOFF:
                break
            if float(row["high"]) >= sneaky_high:
                return self._exit_from(ctx, j, sneaky_high, defended_low,
                                       self.key, slip_bps, target=target)

        return None
