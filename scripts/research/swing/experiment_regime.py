"""Survivorship/regime stress test for breakout_52w.

The first SPY experiment used 63 hand-picked mega-caps over a bull OOS window —
two ways the edge could be an illusion (survivorship + bull beta). This attacks
both, within Schwab's data limits (daily history starts ~2019; no delisted or
point-in-time-constituent data, so this MITIGATES survivorship, not eliminates).

  1. CONCENTRATION: run on a broad ~140-name sector-diverse universe loaded with
     laggards (not just winners) and compare expectancy to the narrow 63.
  2. REGIME: per-calendar-year expectancy + SPY return, isolating 2022 (bear) and
     2020 (COVID crash). A bull-only edge collapses there.

Default config only (no tuning). Simulated. Run in the bot container.
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
NARROW_PATH = "/app/state/breakout_universe.txt"

# Broad, sector-diverse, laggard-heavy universe (mitigates winner-only bias).
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


def _run(bars, syms, cfg):
    out = []
    for s in syms:
        df = bars.get(s)
        if df is None or df.empty:
            continue
        out.extend(breakout_52w_trades(df, s, lookback=cfg["lookback"],
                   ma_exit=cfg["ma_exit"], stop_pct=cfg["stop_pct"], slip_bps=SLIP_BPS))
    return out


def _spy_year_returns(spy):
    by_year = defaultdict(list)
    for d, c in zip(spy.index, spy["close"].to_numpy()):
        by_year[d.year].append(c)
    return {y: (cs[-1] / cs[0] - 1.0) for y, cs in by_year.items() if len(cs) > 1}


def main():
    from pathlib import Path
    from src.bot.config import get_bot_config
    from src.core.schwab_client import SchwabClient
    cfg = get_bot_config()
    client = SchwabClient(app_key=cfg.schwab_app_key, app_secret=cfg.schwab_app_secret,
                          callback_url=cfg.schwab_oauth_redirect_uri, token_path=cfg.schwab_token_path)

    narrow = [s.strip().upper() for s in Path(NARROW_PATH).read_text().split() if s.strip()]
    broad = sorted(set(BROAD))
    to_fetch = sorted(set(broad) | set(narrow) | {"SPY"})
    print(f"Fetching {len(to_fetch)} symbols {FETCH_START}..{END} ...")
    bars = {}
    for s in to_fetch:
        df = client.get_history(s, "1Day", FETCH_START, END)
        if df is not None and not df.empty:
            bars[s] = df
    spy = bars["SPY"]
    got_broad = [s for s in broad if s in bars]
    print(f"Got {len(bars)} symbols ({len(got_broad)}/{len(broad)} broad resolved)\n")

    narrow_t = _run(bars, narrow, CFG)
    broad_t = _run(bars, got_broad, CFG)

    print("=== CONCENTRATION TEST (default cfg, full 2020-2026 window) ===")
    ns, bs = _stats(narrow_t), _stats(broad_t)
    print(f"  Narrow 63   : expectancy {ns['exp']*100:+5.2f}%  n={ns['n']:4d}  t={ns['t']:4.1f}  win {ns['win']*100:.0f}%")
    print(f"  Broad  ~140 : expectancy {bs['exp']*100:+5.2f}%  n={bs['n']:4d}  t={bs['t']:4.1f}  win {bs['win']*100:.0f}%")
    print(f"  -> edge {'HOLDS broad' if bs['t'] > 2 and bs['exp'] > 0 else 'WEAKENS on broad universe'}\n")

    print("=== REGIME TEST (broad universe, expectancy by entry-year vs SPY) ===")
    spy_yr = _spy_year_returns(spy)
    by_year = defaultdict(list)
    for t in broad_t:
        by_year[t["entry_date"].year].append(t)
    print(f"  {'year':6} {'SPY':>8} {'strat exp':>10} {'n':>5} {'win':>5}   regime")
    for y in sorted(by_year):
        st = _stats(by_year[y])
        spyr = spy_yr.get(y, float('nan'))
        regime = "BEAR" if (spyr is not None and spyr < -0.05) else ("crash-yr" if y == 2020 else "")
        print(f"  {y:6} {spyr*100:+7.1f}% {st['exp']*100:+9.2f}% {st['n']:5d} {st['win']*100:4.0f}%   {regime}")

    print("\n=== BROAD universe portfolio vs SPY ===")
    for label, start in [("Full 2020-2026", dt.date(2020, 1, 1)), ("OOS 2024-2026", dt.date(2024, 1, 1))]:
        sub = [t for t in broad_t if t["entry_date"] >= start]
        p = simulate_portfolio(sub, starting_equity=200.0, risk_pct=0.01, max_concurrent=None, min_notional=1.0)
        sub_spy = spy[[start <= d.date() <= END for d in spy.index]]["close"].to_numpy()
        spy_tr = sub_spy[-1] / sub_spy[0] - 1.0
        peak, mdd = sub_spy[0], 0.0
        for c in sub_spy:
            peak = max(peak, c); mdd = max(mdd, (peak - c) / peak)
        print(f"  {label}: strat CAGR {_cagr(p['total_return'], start, END)*100:+5.1f}% maxDD {p['max_drawdown']*100:4.1f}% "
              f"({p['n_taken']} trades)  |  SPY CAGR {_cagr(spy_tr, start, END)*100:+5.1f}% maxDD {mdd*100:4.1f}%")

    print("\nNOTE: still NOT truly survivorship-free — Schwab won't serve delisted/bankrupt "
          "names, so even this broad set is all survivors. Read 2022 + 2020 rows as the real tell.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
