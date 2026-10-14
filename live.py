"""Intraday poller -> live.db `ticks`. Read-only on vol.db. Run: .venv/bin/python live.py [once]
Research only. p is the model score AS OF LAST CLOSE (latest row of scores) — it is not re-scored intraday;
x/days_to_earnings come from features.feats on bars through the last stored close."""
import os, sqlite3, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, yfinance as yf
from data import DB, TICKERS
from features import feats

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.environ.get("LIVE_DB") or os.path.join(HERE, "live.db")
ET = ZoneInfo("America/New_York")
ALL = TICKERS + ["SPY", "^VIX"]
COLS = "ticker, ts, last, prev_close, pct_today, expected_move_5d, implied_move, realised_ratio, p, days_to_earnings, vix, spy_pct, x"


def market_open(now):
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 25 <= m <= 16 * 60 + 5


def static():
    """Per-ticker slow facts from vol.db (refreshed every 30 min)."""
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        q = lambda s: pd.read_sql_query(s, db, parse_dates=["date"])
        f = feats(q("select * from bars"), q("select * from earnings"))
        ern = q("select * from earnings")
        sc = q("select ticker, date, p from scores").sort_values("date").groupby("ticker").p.last()
        ch = pd.read_sql_query("select ticker, date, expiry, implied_move from chain", db).sort_values(["date", "expiry"])
        ch = ch[ch.date == ch.groupby("ticker").date.transform("max")].groupby("ticker").implied_move.first()
    finally: db.close()
    today = pd.Timestamp(datetime.now(ET).date())
    out = {}
    for t in TICKERS:
        g = f[f.ticker == t]
        if g.empty: continue
        r = g.iloc[-1]
        nxt = ern[(ern.ticker == t) & (ern.date >= today)].date.min()
        out[t] = dict(em=float(r.rv20 * np.sqrt(5 / 252)), x=float(r.rv5_rv20), p=float(sc[t]) if t in sc else None,
                      im=float(ch[t]) if t in ch else None, dte=int((nxt - today).days) if pd.notna(nxt) else None)
    return out


def quote(t):
    try:
        fi = yf.Ticker(t).fast_info
        last, prev = fi["last_price"], fi["previous_close"]
        if last and prev and last == last and prev == prev: return float(last), float(prev)
    except Exception: pass
    c = yf.Ticker(t).history(period="5d")["Close"].dropna()
    return float(c.iloc[-1]), float(c.iloc[-2])


def poll(S, lv):
    ts, qs = time.time(), {}
    for t in ALL:
        try: qs[t] = quote(t)
        except Exception as e: print(f"  {t}: {e!r}"[:100])
    pct = lambda t: qs[t][0] / qs[t][1] - 1 if t in qs else None
    vix, spy = qs.get("^VIX", (None,))[0], pct("SPY")
    n = 0
    for t in ALL:
        if t not in qs: continue
        last, prev = qs[t]; s = S.get(t, {})
        em, pt = s.get("em"), last / prev - 1
        lv.execute(f"insert into ticks({COLS}) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (t, ts, last, prev, pt, em, s.get("im"), abs(pt) / em if em else None, s.get("p"), s.get("dte"), vix, spy, s.get("x")))
        n += 1
    lv.commit()
    top = sorted((t for t in TICKERS if t in qs), key=lambda t: -abs(pct(t)))[:3]
    print(datetime.now(ET).strftime("%H:%M:%S"), f"{n}/{len(ALL)}", "SPY", f"{(spy or 0)*100:+.2f}%", "VIX", vix and round(vix, 1),
          " ".join(f"{t} {pct(t)*100:+.1f}%" for t in top), flush=True)
    return n


def main():
    lv = sqlite3.connect(LIVE)
    lv.execute(f"create table if not exists ticks(ticker text, ts real, last real, prev_close real, pct_today real, expected_move_5d real, "
               "implied_move real, realised_ratio real, p real, days_to_earnings integer, vix real, spy_pct real, x real)")
    S, at = {}, 0
    while True:
        try:
            if time.time() - at > 1800 or not S: S, at = static(), time.time()
            poll(S, lv)
        except Exception as e: print("poll failed:", repr(e), flush=True)  # never crash the loop
        if sys.argv[1:] == ["once"]: break
        time.sleep(60 if market_open(datetime.now(ET)) else 300)


if __name__ == "__main__": main()
