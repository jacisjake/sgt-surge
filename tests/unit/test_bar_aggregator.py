from datetime import datetime, timezone

from src.core.schwab_stream import BarAggregator


def _bar(ts_iso: str, o: float, h: float, l: float, c: float, v: int) -> dict:
    return {
        "symbol": "AAPL",
        "timestamp": ts_iso,
        "open": o, "high": h, "low": l, "close": c, "volume": v,
    }


def test_aggregator_emits_one_5min_bar_per_5_inputs():
    emitted: list[dict] = []
    agg = BarAggregator(window_minutes=5, on_emit=lambda b: emitted.append(b))

    inputs = [
        _bar("2026-05-08T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 100),
        _bar("2026-05-08T13:31:00+00:00", 10.1, 10.3, 10.0, 10.25, 200),
        _bar("2026-05-08T13:32:00+00:00", 10.25, 10.4, 10.2, 10.3, 150),
        _bar("2026-05-08T13:33:00+00:00", 10.3, 10.5, 10.25, 10.45, 250),
        _bar("2026-05-08T13:34:00+00:00", 10.45, 10.6, 10.4, 10.55, 300),
    ]
    for b in inputs:
        agg.feed(b)

    agg.feed(_bar("2026-05-08T13:35:00+00:00", 10.55, 10.6, 10.5, 10.58, 100))

    assert len(emitted) == 1
    out = emitted[0]
    assert out["symbol"] == "AAPL"
    assert out["open"] == 10.0
    assert out["high"] == 10.6
    assert out["low"] == 9.9
    assert out["close"] == 10.55
    assert out["volume"] == 1000


def test_aggregator_does_not_emit_partial_bar():
    emitted: list[dict] = []
    agg = BarAggregator(window_minutes=5, on_emit=lambda b: emitted.append(b))
    agg.feed(_bar("2026-05-08T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 100))
    agg.feed(_bar("2026-05-08T13:31:00+00:00", 10.1, 10.3, 10.0, 10.25, 200))
    assert emitted == []
