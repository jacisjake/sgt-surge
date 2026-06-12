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
            # after a drive, look for a pullback bar (lower close than the prior bar)...
            if not pulled_back:
                prev = bars.iloc[i - 1]
                if float(row["close"]) < float(prev["close"]):
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
