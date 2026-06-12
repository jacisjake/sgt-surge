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
