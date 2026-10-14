"""Once per trading day, record the yfinance options chain for the two nearest expiries (>= 2 calendar days out)
into vol.db table `chain`. `python record_chain.py` records once; `python record_chain.py loop` records, then
repeats at 16:15 America/New_York on weekdays. VOL_DB overrides the database path.
"""
import os, sqlite3, sys, time
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo
import pandas as pd, yfinance as yf

DB = os.environ.get("VOL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vol.db"))
TICKERS = ["AMZN", "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL"]  # edit freely
NY = ZoneInfo("America/New_York")
SCHEMA = """create table if not exists chain(ticker text, date text, expiry text, spot real, atm_strike real,
call_mid real, put_mid real, call_iv real, put_iv real, implied_move real, primary key(ticker, date, expiry));"""
INSERT = "insert or replace into chain values(?,?,?,?,?,?,?,?,?,?)"


def mid(bid, ask, last):
    return (bid + ask) / 2 if bid > 0 and ask > 0 else last


def implied(cm, pm, spot):
    return (cm + pm) / spot


def pick(calls, puts, spot):
    """-> (atm_strike, call_mid, put_mid, call_iv, put_iv) at the listed strike (on both sides) nearest spot"""
    m = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
    r = m.loc[(m.strike - spot).abs().idxmin()]
    return (float(r.strike), float(mid(r.bid_c, r.ask_c, r.lastPrice_c)), float(mid(r.bid_p, r.ask_p, r.lastPrice_p)),
            float(r.impliedVolatility_c), float(r.impliedVolatility_p))


def spot_of(t):
    p = t.fast_info.last_price
    if not p or p != p:  # None or NaN
        p = float(t.history(period="5d")["Close"].iloc[-1])
    return float(p)


def fetch(sym, today):
    t = yf.Ticker(sym)
    spot = spot_of(t)
    exps = [e for e in t.options if (date.fromisoformat(e) - today).days >= 2][:2]
    rows = []
    for exp in exps:
        ch = t.option_chain(exp)
        k, cm, pm, ci, pi = pick(ch.calls, ch.puts, spot)
        rows.append((sym, today.isoformat(), exp, spot, k, cm, pm, ci, pi, implied(cm, pm, spot)))
    return rows


def with_retry(fn, sym, *a):
    try:
        return fn(sym, *a)
    except Exception as e:
        print(f"{sym} error: {e}; retrying in 5s", flush=True)
        time.sleep(5)
        return fn(sym, *a)


def record(db):
    today = datetime.now(NY).date()
    for sym in TICKERS:
        try:
            rows = with_retry(fetch, sym, today)
        except Exception as e:
            print(f"{sym} failed: {e}", flush=True)
            continue
        db.executemany(INSERT, rows)
        db.commit()
        if rows:
            r = rows[0]
            print(f"{r[1]} {sym:5} spot {r[3]:.2f}  expiry {r[2]}  implied move {r[9] * 100:.2f}%", flush=True)
        else:
            print(f"{sym} no expiry >= 2 days out", flush=True)


def seconds_until_next():
    now = datetime.now(NY)
    t = now.replace(hour=16, minute=15, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    while t.weekday() >= 5:
        t += timedelta(days=1)
    return (t.astimezone(UTC) - now.astimezone(UTC)).total_seconds()  # UTC diff so DST doesn't shift the sleep


if __name__ == "__main__":
    db = sqlite3.connect(DB, timeout=30)
    db.executescript(SCHEMA)
    record(db)
    if sys.argv[1:] == ["loop"]:
        while True:
            time.sleep(seconds_until_next())
            record(db)
