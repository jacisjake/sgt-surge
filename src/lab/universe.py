"""Screening predicate for the lab trading universe.

Pure functions over plain dicts — no network, no client, no pandas — so the
screen can be unit-tested without touching Schwab or TradingView. The I/O that
builds the rows lives in ``scripts/lab/build_universe.py``.

Why each filter exists:

- **price band** — sizing is ``risk_pct * equity / stop_frac`` (see
  ``src/lab/fills/broker.py``), roughly $24/position on a $200 account. Above
  that ceiling every fill is a fraction of a share.
- **median dollar volume** — a *median*, not a mean, so a single volume spike
  cannot qualify an otherwise untradeable name.
- **bar count** — ``breakout_52w`` uses a 252-bar lookback; a symbol with a
  shorter history can never generate a signal, so it is dead weight.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Sequence

# Defaults are deliberately conservative; the CLI exposes each as a flag.
DEFAULT_PARAMS: dict[str, float] = {
    "price_min": 3.0,
    "price_max": 25.0,
    "min_dollar_vol": 5_000_000.0,
    "min_bars": 280,  # 252 lookback + slack for holidays / partial history
}


def median_dollar_volume(
    closes: Sequence[float], volumes: Sequence[float]
) -> float:
    """Median of close*volume across the series. 0.0 when there is no data."""
    pairs = [float(c) * float(v) for c, v in zip(closes, volumes)]
    if not pairs:
        return 0.0
    return float(median(pairs))


def qualifies(row: dict[str, Any], params: dict[str, float]) -> bool:
    """True when a candidate clears the price band, liquidity and history floors.

    ``row`` carries ``last_close``, ``median_dollar_vol`` and ``n_bars``.
    """
    last_close = float(row.get("last_close") or 0.0)
    if not (params["price_min"] <= last_close <= params["price_max"]):
        return False
    if float(row.get("median_dollar_vol") or 0.0) < params["min_dollar_vol"]:
        return False
    if int(row.get("n_bars") or 0) < params["min_bars"]:
        return False
    return True


def screen(rows: list[dict[str, Any]], params: dict[str, float]) -> list[str]:
    """Qualifying symbols, deduplicated and sorted so output is reproducible."""
    return sorted({
        str(r["symbol"]).upper()
        for r in rows
        if r.get("symbol") and qualifies(r, params)
    })
