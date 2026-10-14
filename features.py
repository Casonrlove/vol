"""dataset(db): one row per (ticker, date). Every feature uses data <= date; labels joined at the end."""
import numpy as np, pandas as pd, sqlite3
from data import DB, TICKERS

LABELS = ["expected_move_5d", "fwd_move_5d", "ratio", "rv_expand", "excess_ratio", "beat_implied"]


def one(g):
    g = g.set_index("date")
    c, r = np.log(g.close), np.log(g.close).diff()
    rv = lambda n: r.rolling(n).std() * np.sqrt(252)
    v = g.volume.rolling(20)
    return pd.DataFrame(dict(
        ticker=g.ticker.iloc[0], rv5=rv(5), rv20=rv(20), rv60=rv(60), rv5_rv20=rv(5) / rv(20), rv20_rv60=rv(20) / rv(60),
        ret_1d=r, ret_5d=c.diff(5), ret_20d=c.diff(20), gap=g.open / g.close.shift() - 1,
        vol_z=(g.volume - v.mean()) / v.std(), range_pct=(g.high - g.low) / g.close,
        day_of_week=g.index.dayofweek, month=g.index.month)).reset_index()


def feats(bars, earn, tickers=TICKERS):
    """bars: ticker,date,open,high,low,close,volume. earn: ticker,date. Returns features only (no labels)."""
    bars = bars.sort_values(["ticker", "date"])
    f = pd.concat([one(g) for t, g in bars.groupby("ticker") if t in tickers])
    spy = f[f.ticker == "SPY"] if "SPY" in tickers else one(bars[bars.ticker == "SPY"])
    spy = spy.set_index("date")[["ret_5d", "rv20"]].add_prefix("spy_")
    vix = bars[bars.ticker == "^VIX"].set_index("date").close
    mkt = spy.join(pd.DataFrame(dict(vix_close=vix, vix_chg_5d=vix.pct_change(5))))
    f = f.join(mkt, on="date").sort_values("date")
    e = earn.assign(ed=earn.date).sort_values("date")
    # next scheduled earnings on/after date (lookahead-sensitive: assumes dates were announced ahead), last on/before date
    f = pd.merge_asof(f, e.rename(columns={"ed": "next_e"}), on="date", by="ticker", direction="forward")
    f = pd.merge_asof(f, e.rename(columns={"ed": "last_e"}), on="date", by="ticker", direction="backward")
    f["days_to_next_earnings"] = (f.next_e - f.date).dt.days
    f["days_since_last_earnings"] = (f.date - f.last_e).dt.days
    first = e.groupby("ticker").date.min()  # before the earliest known report, "next/last" would be wrong
    f = f[f.date >= f.ticker.map(first)]
    return f.drop(columns=["next_e", "last_e"]).sort_values(["ticker", "date"]).reset_index(drop=True)


def dataset(db):
    q = lambda s: pd.read_sql_query(s, db, parse_dates=["date"])
    f = feats(q("select * from bars"), q("select * from earnings"))
    lab = q("select ticker, date, " + ", ".join(LABELS) + " from labels")
    return f.merge(lab, on=["ticker", "date"]).sort_values(["date", "ticker"]).reset_index(drop=True)


if __name__ == "__main__":
    d = dataset(sqlite3.connect(DB)); print(d.shape); print(d.tail(3).T)
