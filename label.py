"""Label (ticker, day t): did the stock move more than expected over the next 5 trading rows? Idempotent.

rv_expand (|fwd 5d move| > rv20-implied 5d move) is a PROXY. The real label is beat_implied
(fwd_move_5d > options-implied move from the `chain` table), left NULL when no chain row exists.
"""
import os, sqlite3
import numpy as np, pandas as pd
from data import DB, TICKERS

H = 5


def one(g, spy_r, spy_f):
    c = np.log(g.set_index("date").close)
    r = c.diff()
    s = spy_r.reindex(c.index)
    beta = r.rolling(60).cov(s) / s.rolling(60).var()
    rv20 = r.rolling(20).std() * np.sqrt(252)
    exp = rv20 * np.sqrt(H / 252)
    f = c.shift(-H) - c  # trading-row shift, never calendar days
    ex = f - beta * spy_f.reindex(c.index)
    return pd.DataFrame(dict(ticker=g.ticker.iloc[0], rv20=rv20, expected_move_5d=exp, fwd_move_5d=f.abs(),
                             ratio=f.abs() / exp, rv_expand=(f.abs() / exp > 1).astype(int), excess_ratio=ex.abs() / exp,
                             fwd_ret_5d=f)).reset_index()


def compute(bars, tickers=TICKERS):
    bars = bars.sort_values(["ticker", "date"])
    spy = np.log(bars[bars.ticker == "SPY"].set_index("date").close)
    out = pd.concat([one(g, spy.diff(), spy.shift(-H) - spy) for t, g in bars.groupby("ticker") if t in tickers])
    return out.dropna(subset=["fwd_move_5d", "rv20", "excess_ratio"]).drop(columns="fwd_ret_5d").reset_index(drop=True)


def run(db):
    bars = pd.read_sql_query("select ticker, date, close from bars", db, parse_dates=["date"])
    out = compute(bars)
    out["beat_implied"] = np.nan
    if db.execute("select 1 from sqlite_master where name='chain'").fetchone():
        ch = pd.read_sql_query("select ticker, date, avg(implied_move) im from chain group by 1, 2", db, parse_dates=["date"])
        out = out.merge(ch, on=["ticker", "date"], how="left")
        out["beat_implied"] = (out.fwd_move_5d > out.im).astype(float).where(out.im.notna())
        out = out.drop(columns="im")
    out["date"] = out.date.dt.strftime("%Y-%m-%d")
    out.to_sql("labels", db, if_exists="replace", index=False)
    db.commit()
    return out


if __name__ == "__main__":
    o = run(sqlite3.connect(DB))
    print(len(o), "labels; rv_expand base rate", round(o.rv_expand.mean(), 3), "; beat_implied non-null", int(o.beat_implied.notna().sum()))
