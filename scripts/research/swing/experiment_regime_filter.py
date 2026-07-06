"""Test a market-regime filter on breakout_52w.

Hypothesis: most of the 2021-chop / 2022-bear bleed comes from taking long
breakouts while the broad market is in a downtrend. Gate every entry on
SPY close > SPY 200-day SMA on the entry date (causal — SMA uses only past
data). Compare filtered vs unfiltered head-to-head; the win condition is
cutting the bear-year losses without gutting the trending-year returns.

Default cfg, broad universe. Simulated. Run in the bot container.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from scripts.research.swing.portfolio import simulate_portfolio  # noqa: E402
from scripts.research.swing.strategies import breakout_52w_trades  # noqa: E402

FETCH_START = dt.date(2019, 1, 1)
END = dt.date(2026, 6, 30)
SLIP_BPS = 15.0
CFG = {"lookback": 252, "ma_exit": 50, "stop_pct": 0.08}
SPY_SMA = 200

BROAD = """
NVDA MSFT AAPL GOOGL AMZN META AVGO AMD TSLA NFLX CRM ADBE ORCL QCOM TXN CSCO IBM
INTC MU AMAT MRVL HPQ DELL PYPL SNAP PINS ROKU U PARA WBD CMCSA DIS T VZ TMUS
JPM BAC WFC C GS MS USB PNC TFC AXP V MA BLK SCHW COF SPGI
UNH JNJ LLY ABBV MRK TMO ABT DHR BMY AMGN GILD CVS CI PFE MRNA BIIB
PG KO PEP WMT COST HD LOW MCD SBUX NKE TGT KHC CL KMB GIS WBA KR DG DLTR
CAT DE GE HON UPS FDX RTX LMT BA MMM EMR ETN DOW EMN
XOM CVX COP SLB EOG OXY PSX VLO MPC KMI WMB HAL DVN APA
F GM HOG DAL AAL UAL LUV CCL NCLH RCL MGM WYNN LVS
KSS M GPS JWN BBY URBN
NEM FCX X CLF NUE
D SO DUK EXC AEP ED PEG
SPY QQQ IWM XLF XLE XLK XLV XLY XLP XLI XLU XLB XLRE
""".split()


def _stats(trades):
    rets = [t["return_pct"] for t in trades]
    n = len(rets)
    if n == 0:
        return {"n": 0, "exp": 0.0, "win": 0.0, "t": 0.0}
    mean = sum(rets) / n
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "exp": mean, "win": sum(1 for r in rets if r > 0) / n,
            "t": (mean / (std / math.sqrt(n))) if std > 0 else 0.0}


def _cagr(tr, start, end):
    yrs = max((end - start).days / 365.25, 1e-9)
    return (1 + tr) ** (1 / yrs) - 1


def _port_vs_spy(trades, spy, start, label):
    sub = [t for t in trades if t["entry_date"] >= start]
    p = simulate_portfolio(sub, starting_equity=200.0, risk_pct=0.01,
                           max_concurrent=None, min_notional=1.0)
    closes = spy[[start <= d.date() <= END for d in spy.index]]["close"].to_numpy()
    spy_tr = closes[-1] / closes[0] - 1.0
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c); mdd = max(mdd, (peak - c) / peak)
    print(f"  {label:22} strat CAGR {_cagr(p['total_return'], start, END)*100:+5.1f}% "
          f"maxDD {p['max_drawdown']*100:4.1f}% ({p['n_taken']} trades)  |  "
          f"SPY {_cagr(spy_tr, start, END)*100:+5.1f}% / {mdd*100:4.1f}%")


def main():
    from pathlib import Path
    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient
    cfg = get_bot_config()
    client = SchwabClient(app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
                          callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path)

    broad = sorted(set(BROAD))
    print(f"Fetching {len(broad)} symbols {FETCH_START}..{END} ...")
    bars = {}
    for s in broad:
        df = client.get_history(s, "1Day", FETCH_START, END)
        if df is not None and not df.empty:
            bars[s] = df
    spy = bars["SPY"]

    # Causal risk-on map: SPY close > SMA200 on each date (NaN warmup -> risk-off).
    sma = spy["close"].rolling(SPY_SMA).mean().to_numpy()
    closes = spy["close"].to_numpy()
    risk_on = {}
    for i, d in enumerate(spy.index):
        risk_on[d.date()] = (not math.isnan(sma[i])) and (closes[i] > sma[i])

    all_t = []
    for s in bars:
        df = bars[s]
        all_t.extend(breakout_52w_trades(df, s, lookback=CFG["lookback"],
                     ma_exit=CFG["ma_exit"], stop_pct=CFG["stop_pct"], slip_bps=SLIP_BPS))
    filt_t = [t for t in all_t if risk_on.get(t["entry_date"], False)]

    us, fs = _stats(all_t), _stats(filt_t)
    print(f"\n=== FULL 2020-2026: unfiltered vs SPY>200dma filter ===")
    print(f"  Unfiltered : exp {us['exp']*100:+5.2f}%  n={us['n']:4d}  t={us['t']:4.1f}  win {us['win']*100:.0f}%")
    print(f"  Filtered   : exp {fs['exp']*100:+5.2f}%  n={fs['n']:4d}  t={fs['t']:4.1f}  win {fs['win']*100:.0f}%  "
          f"(kept {fs['n']/us['n']*100:.0f}% of trades)")

    print(f"\n=== Per-year expectancy: unfiltered -> filtered ===")
    by_u, by_f = defaultdict(list), defaultdict(list)
    for t in all_t:
        by_u[t["entry_date"].year].append(t)
    for t in filt_t:
        by_f[t["entry_date"].year].append(t)
    print(f"  {'year':6} {'unfilt exp':>11} {'n':>5}   {'filt exp':>9} {'n':>5}")
    for y in sorted(by_u):
        u, f = _stats(by_u[y]), _stats(by_f[y])
        print(f"  {y:6} {u['exp']*100:+10.2f}% {u['n']:5d}   {f['exp']*100:+8.2f}% {f['n']:5d}")

    print(f"\n=== Portfolio vs SPY ===")
    for start, lbl in [(dt.date(2020, 1, 1), "Full unfiltered"), (dt.date(2024, 1, 1), "OOS unfiltered")]:
        _port_vs_spy(all_t, spy, start, lbl)
    for start, lbl in [(dt.date(2020, 1, 1), "Full FILTERED"), (dt.date(2024, 1, 1), "OOS FILTERED")]:
        _port_vs_spy(filt_t, spy, start, lbl)

    print("\nWin condition: filter lifts full-period expectancy/t and cuts 2021-2022 "
          "losses without gutting 2023-2026.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
