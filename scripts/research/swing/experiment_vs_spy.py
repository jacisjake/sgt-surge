"""Walk-forward experiment: breakout_52w vs SPY buy-and-hold.

Answers the only question that matters: does the edge survive OUT OF SAMPLE,
and does it beat just holding SPY over the same window, after slippage?

Method:
  1. Fetch daily bars for the universe + SPY over the full window (one fetch).
  2. Grid-search breakout_52w params on the IN-SAMPLE period (entry < SPLIT),
     pick the config with the best in-sample portfolio return.
  3. Apply that config UNCHANGED to the OUT-OF-SAMPLE period (entry >= SPLIT).
     Also run the naive default config OOS as a non-cherry-picked control.
  4. Report edge significance (t-stat on per-trade returns), CAGR, and max DD
     vs SPY buy-and-hold over the OOS window.

Overfit tell: in-sample-best expectancy collapses (or goes negative) OOS.
Simulated only. Run inside the bot container (needs Schwab token + deps).
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from itertools import product

sys.path.insert(0, "/app")

from scripts.research.swing.portfolio import simulate_portfolio  # noqa: E402
from scripts.research.swing.strategies import breakout_52w_trades  # noqa: E402

UNIVERSE_PATH = "/app/state/breakout_universe.txt"
FETCH_START = dt.date(2019, 1, 1)
END = dt.date(2026, 6, 30)
SPLIT = dt.date(2024, 1, 1)  # in-sample < SPLIT <= out-of-sample
SLIP_BPS = 15.0
START_EQUITY = 200.0
RISK_PCT = 0.01

GRID = {
    "lookback": [126, 252],
    "ma_exit": [20, 50, 100],
    "stop_pct": [0.05, 0.08, 0.12],
}
DEFAULT = {"lookback": 252, "ma_exit": 50, "stop_pct": 0.08}


def _trade_stats(trades: list[dict]) -> dict:
    rets = [t["return_pct"] for t in trades]
    n = len(rets)
    if n == 0:
        return {"n": 0, "expectancy": 0.0, "win_rate": 0.0, "tstat": 0.0}
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    tstat = (mean / (std / math.sqrt(n))) if std > 0 else 0.0
    return {
        "n": n,
        "expectancy": mean,
        "win_rate": sum(1 for r in rets if r > 0) / n,
        "tstat": tstat,
    }


def _cagr(total_return: float, start: dt.date, end: dt.date) -> float:
    years = max((end - start).days / 365.25, 1e-9)
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _spy_buy_hold(spy_df, start: dt.date, end: dt.date) -> dict:
    sub = spy_df[[start <= d.date() <= end for d in spy_df.index]]
    closes = sub["close"].to_numpy()
    if len(closes) < 2:
        return {}
    daily = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    mean = sum(daily) / len(daily)
    var = sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
    # max drawdown on close series
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = max(mdd, (peak - c) / peak)
    total_return = closes[-1] / closes[0] - 1.0
    return {"total_return": total_return, "cagr": _cagr(total_return, start, end),
            "max_dd": mdd, "sharpe": sharpe, "n_days": len(closes)}


def _run_config(bars, symbols, cfg) -> list[dict]:
    trades = []
    for sym in symbols:
        df = bars.get(sym)
        if df is None or df.empty:
            continue
        trades.extend(breakout_52w_trades(
            df, sym, lookback=cfg["lookback"], ma_exit=cfg["ma_exit"],
            stop_pct=cfg["stop_pct"], slip_bps=SLIP_BPS))
    return trades


def _split(trades):
    is_t = [t for t in trades if t["entry_date"] < SPLIT]
    oos_t = [t for t in trades if t["entry_date"] >= SPLIT]
    return is_t, oos_t


def _portfolio(trades):
    return simulate_portfolio(trades, starting_equity=START_EQUITY,
                              risk_pct=RISK_PCT, max_concurrent=None, min_notional=1.0)


def main() -> int:
    from pathlib import Path
    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient

    symbols = [s.strip().upper() for s in Path(UNIVERSE_PATH).read_text().split() if s.strip()]
    cfg = get_bot_config()
    client = SchwabClient(app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
                          callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path)

    print(f"Fetching {len(symbols)} symbols + SPY, {FETCH_START}..{END} ...")
    bars = {}
    for sym in symbols:
        df = client.get_history(sym, "1Day", FETCH_START, END)
        if df is not None and not df.empty:
            bars[sym] = df
    spy = client.get_history("SPY", "1Day", FETCH_START, END)
    data_start = min(d.date() for df in bars.values() for d in [df.index[0]])
    print(f"Got {len(bars)} symbols. Earliest bar: {data_start}. "
          f"IS: {data_start}..{SPLIT}  OOS: {SPLIT}..{END}\n")

    # Grid search on IN-SAMPLE.
    print("Grid search on in-sample (ranked by IS portfolio return):")
    ranked = []
    for lb, me, sp in product(GRID["lookback"], GRID["ma_exit"], GRID["stop_pct"]):
        c = {"lookback": lb, "ma_exit": me, "stop_pct": sp}
        is_t, _ = _split(_run_config(bars, symbols, c))
        p = _portfolio(is_t)
        st = _trade_stats(is_t)
        ranked.append((p["total_return"], c, st, p))
    ranked.sort(key=lambda x: x[0], reverse=True)
    for tr, c, st, p in ranked[:5]:
        print(f"  IS ret {tr*100:7.1f}%  n={st['n']:4d}  exp={st['expectancy']*100:+5.2f}%  "
              f"t={st['tstat']:5.2f}  cfg={c}")
    best_cfg = ranked[0][1]
    print(f"\nIS-best config: {best_cfg}\n")

    spy_oos = _spy_buy_hold(spy, SPLIT, END)

    def report(label, cfg_used):
        all_t = _run_config(bars, symbols, cfg_used)
        is_t, oos_t = _split(all_t)
        is_st, oos_st = _trade_stats(is_t), _trade_stats(oos_t)
        oos_p = _portfolio(oos_t)
        print(f"=== {label}: {cfg_used} ===")
        print(f"  IS  expectancy {is_st['expectancy']*100:+5.2f}%  (n={is_st['n']}, t={is_st['tstat']:.2f})")
        print(f"  OOS expectancy {oos_st['expectancy']*100:+5.2f}%  (n={oos_st['n']}, t={oos_st['tstat']:.2f}, "
              f"win {oos_st['win_rate']*100:.0f}%)   <-- overfit if this collapses vs IS")
        print(f"  OOS portfolio  return {oos_p['total_return']*100:+.1f}%  "
              f"CAGR {_cagr(oos_p['total_return'], SPLIT, END)*100:+.1f}%  "
              f"maxDD {oos_p['max_drawdown']*100:.1f}%  trades {oos_p['n_taken']}\n")

    report("IS-BEST applied OOS", best_cfg)
    report("DEFAULT (control) OOS", DEFAULT)

    print(f"=== SPY buy-and-hold, OOS {SPLIT}..{END} ===")
    if spy_oos:
        print(f"  return {spy_oos['total_return']*100:+.1f}%  CAGR {spy_oos['cagr']*100:+.1f}%  "
              f"maxDD {spy_oos['max_dd']*100:.1f}%  Sharpe {spy_oos['sharpe']:.2f}")
    print("\nVerdict guide: edge is real only if OOS t-stat > ~2 AND OOS CAGR beats SPY "
          "at comparable/!lower maxDD. If IS-best collapses OOS, it was curve-fit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
