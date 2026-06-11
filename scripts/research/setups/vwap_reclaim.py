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
