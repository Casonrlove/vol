"""Self-check: python test_features.py"""
import os, sqlite3
import numpy as np, pandas as pd
import features, label, train
from data import DB

rng = np.random.default_rng(0)
dates = pd.bdate_range("2020-01-01", periods=300)


def mk(t, drift=0.0):
    c = 100 * np.exp(np.cumsum(rng.normal(drift, .02, len(dates))))
    return pd.DataFrame(dict(ticker=t, date=dates, open=c * (1 + rng.normal(0, .003, len(dates))), high=c * 1.01,
                             low=c * .99, close=c, volume=rng.integers(1e6, 2e6, len(dates)).astype(float)))


bars = pd.concat([mk("T"), mk("SPY"), mk("^VIX")])
E = dates[150]
earn = pd.DataFrame(dict(ticker="T", date=[dates[60], E, dates[240]]))
F = lambda b: features.feats(b, earn, ["T"])

# no lookahead: scramble everything strictly after cutoff, features at/before cutoff unchanged
k = 200; cut = dates[k]
b2 = bars.copy(); fut = b2.date > cut
for c in ["open", "high", "low", "close", "volume"]: b2.loc[fut, c] = b2.loc[fut, c] * rng.uniform(.3, 3, fut.sum())
f1, f2 = F(bars), F(b2)
pd.testing.assert_frame_equal(f1[f1.date <= cut].reset_index(drop=True), f2[f2.date <= cut].reset_index(drop=True))
assert not f1[f1.date > cut].drop(columns=["date"]).equals(f2[f2.date > cut].drop(columns=["date"]))  # sanity: test can fail

# earnings distances (calendar days) around E
f = f1.set_index("date")
assert f.index.min() == dates[60]  # nothing before the first known earnings date
for d in (dates[100], dates[149], E):
    assert f.loc[d, "days_to_next_earnings"] == (E - d).days, d
assert f.loc[E, "days_to_next_earnings"] == 0 and f.loc[E, "days_since_last_earnings"] == 0
nxt = dates[151]; assert f.loc[nxt, "days_since_last_earnings"] == (nxt - E).days
assert f.loc[nxt, "days_to_next_earnings"] == (dates[240] - nxt).days

# label forward window is trading rows: drop some rows (holes) and compare positionally
bh = bars[~bars.date.isin(dates[[40, 41, 90]])].reset_index(drop=True)
L = label.compute(bh, ["T"]).set_index("date")
t = bh[bh.ticker == "T"].reset_index(drop=True)
i = 100; d0 = t.date[i]
assert abs(L.loc[d0, "fwd_move_5d"] - abs(np.log(t.close[i + 5] / t.close[i]))) < 1e-12
assert abs(L.loc[d0, "ratio"] - L.loc[d0, "fwd_move_5d"] / L.loc[d0, "expected_move_5d"]) < 1e-12
assert L.index.max() == t.date[len(t) - 6] and t.date[len(t) - 5] not in L.index  # last 5 rows: no label
r = np.log(t.close).diff()
assert abs(L.loc[d0, "rv20"] - r[i - 19:i + 1].std() * np.sqrt(252)) < 1e-12

# split is time-ordered across tickers (purged)
big = pd.DataFrame(dict(date=np.repeat(pd.bdate_range("2019-01-01", "2024-12-31"), 3))).sort_values("date").reset_index(drop=True)
a, b, te = train.split(big)
assert big.date[a].max() <= big.date[b].min() and big.date[b].max() < train.CUT <= big.date[te].min()
assert len(a) + len(b) + len(te) < len(big) and big.date[te].min() >= train.CUT

# real data: auto_adjust'd AMZN has no 20x split jump
if os.path.exists(DB):
    rows = sqlite3.connect(DB).execute("select close from bars where ticker='AMZN' order by date").fetchall()
    if rows:
        c = np.array([x[0] for x in rows]); j = np.abs(np.log(c[1:] / c[:-1])).max()
        assert j < np.log(3), j
        print("AMZN max daily |log ret|", round(j, 3), "over", len(c), "bars")
print("ok")
