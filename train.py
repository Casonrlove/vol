"""python train.py [rv_expand|beat_implied] -- time-ordered split across tickers, LightGBM binary, threshold table on untouched test."""
import sqlite3, sys
import lightgbm as lgb, numpy as np, pandas as pd
from features import DB, dataset, LABELS

CUT = pd.Timestamp("2023-01-01")
PURGE = pd.Timedelta(days=8)  # 5d forward labels overlap the cutoff: drop train rows whose window reaches test
P = dict(objective="binary", num_leaves=15, learning_rate=.03, min_data_in_leaf=100, feature_fraction=.8, verbose=-1)


def split(df, cut=CUT):
    """df sorted by date -> (train, val, test) positional index arrays; val = last 10% of train."""
    tr = np.flatnonzero(df.date < cut - PURGE); te = np.flatnonzero(df.date >= cut)
    k = int(len(tr) * .9)
    return tr[:k], tr[k:], te


def auc(y, p):
    r = pd.Series(p).rank().to_numpy(); n1 = y.sum()
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1))


def main(target="rv_expand", db_path=DB):
    db = sqlite3.connect(db_path)
    df = dataset(db)
    df = df[df[target].notna()].sort_values("date").reset_index(drop=True)
    if target == "beat_implied" and len(df) < 500: sys.exit(f"only {len(df)} non-null beat_implied rows, need 500")
    X, y = df.drop(columns=["ticker", "date"] + LABELS), df[target].astype(int).to_numpy()
    a, b, t = split(df)
    m = lgb.train(P, lgb.Dataset(X.iloc[a], y[a]), 1000, valid_sets=[lgb.Dataset(X.iloc[b], y[b])],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
    m.save_model(f"model_{target}.txt")
    p = m.predict(X, num_iteration=m.best_iteration)
    yt, pt, te = y[t], p[t], df.iloc[t]
    ll = -np.mean(yt * np.log(np.clip(pt, 1e-15, 1)) + (1 - yt) * np.log(np.clip(1 - pt, 1e-15, 1)))
    print(f"target={target} train={len(a)} val={len(b)} test={len(t)} best_iter={m.best_iteration}")
    print(f"test AUC={auc(yt, pt):.4f} logloss={ll:.4f} base_rate={yt.mean():.4f}")
    d = (te.fwd_move_5d - te.expected_move_5d).to_numpy() * 100
    print("thr   entered  hit_rate  mean(fwd-expected)%  <- delta-neutral proxy, NOT options P&L")
    for th in (.5, .55, .6, .65, .7):
        k = pt >= th
        print(f"{th:<5} {k.sum():<8} " + (f"{yt[k].mean():<9.3f} {d[k].mean():+.3f}" if k.any() else "n/a"))
    sp = np.full(len(df), "train", dtype=object); sp[b] = "val"; sp[t] = "test";
    sp[np.setdiff1d(np.arange(len(df)), np.concatenate([a, b, t]))] = "purged"
    db.execute("drop table if exists scores")
    db.execute("create table scores(ticker text, date text, p real, split text, primary key(ticker, date))")
    db.executemany("insert into scores values(?,?,?,?)",
                   zip(df.ticker, df.date.dt.strftime("%Y-%m-%d"), map(float, p), sp))
    db.commit()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "rv_expand")
