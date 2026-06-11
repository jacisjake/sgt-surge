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
