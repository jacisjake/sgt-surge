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
