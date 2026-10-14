"""Download daily bars + earnings dates into vol.db. Idempotent (insert or replace)."""
import os, sqlite3
import pandas as pd, yfinance as yf

DB = os.environ.get("VOL_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "vol.db")
TICKERS = ["AMZN", "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL"]  # edit freely
CONTEXT = ["SPY", "^VIX"]
COLS = ["Open", "High", "Low", "Close", "Volume"]


def save_bars(db, t, h):
    h = h[COLS].dropna(subset=["Close"])
    rows = [(t, d.strftime("%Y-%m-%d"), *map(float, r)) for d, r in zip(h.index, h.to_numpy())]
    db.executemany("insert or replace into bars values(?,?,?,?,?,?,?)", rows)
    return rows


def run(db):
    db.execute("create table if not exists bars(ticker text, date text, open real, high real, low real, close real, "
               "volume real, primary key(ticker, date))")
    db.execute("create table if not exists earnings(ticker text, date text, primary key(ticker, date))")
    for t in TICKERS + CONTEXT:
        rows = save_bars(db, t, yf.Ticker(t).history(period="max", auto_adjust=True))
        print(f"{t}: {len(rows)} bars {rows[0][1]}..{rows[-1][1]}")
    for t in TICKERS:
        e = yf.Ticker(t).get_earnings_dates(limit=60)
        ds = sorted({d.strftime("%Y-%m-%d") for d in e.index})
        db.executemany("insert or replace into earnings values(?,?)", [(t, d) for d in ds])
        print(f"{t}: {len(ds)} earnings dates {ds[0]}..{ds[-1]}")
    try:
        h = yf.download("^VXAZN", period="max", auto_adjust=True, progress=False)
        if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.droplevel(1)
        if len(h) > 500:
            rows = save_bars(db, "^VXAZN", h)
            print(f"^VXAZN: {len(rows)} rows {rows[0][1]}..{rows[-1][1]}")
        else:
            print(f"^VXAZN: dead/short ({len(h)} rows), skipped")
    except Exception as ex:
        print("^VXAZN: failed", repr(ex))
    db.commit()


if __name__ == "__main__":
    run(sqlite3.connect(DB))
