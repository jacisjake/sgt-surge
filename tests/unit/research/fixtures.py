import pandas as pd

def make_day(session_closes, session_highs, session_lows,
             session_opens=None, volumes=None, pm=True):
    """Build a UTC-indexed 5-min day. First bar is a 09:25 ET pre-market bar
    (if pm), followed by 09:30+ session bars matching the given lists."""
    n = len(session_closes)
    opens = session_opens or [c for c in session_closes]
    vols = volumes or [2000] * n
    rows_t, o, h, l, c, v = [], [], [], [], [], []
    if pm:
        rows_t.append("13:25"); o.append(8.0); h.append(8.2); l.append(7.9); c.append(8.1); v.append(500)
    for i in range(n):
        mins = 30 + i * 5
        rows_t.append(f"13:{mins:02d}" if mins < 60 else f"14:{mins-60:02d}")
        o.append(opens[i]); h.append(session_highs[i]); l.append(session_lows[i])
        c.append(session_closes[i]); v.append(vols[i])
    idx = pd.to_datetime([f"2026-06-09T{t}:00Z" for t in rows_t])
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)
