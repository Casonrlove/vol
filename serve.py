"""Serve the console and tail vol.db to websocket clients. Read-only: never writes, never locks the recorder.
Message shapes (shared with export_replay.py): `row` = one (ticker, date), `day` = cumulative stats as-of a date."""
import asyncio, json, os, sqlite3
import numpy as np, pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from datetime import datetime
from features import dataset
from live import market_open, LIVE, ET

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("VOL_DB") or os.path.join(HERE, "vol.db")
LAG = 5  # a row's label is only known 5 trading days later
app = FastAPI()
clients = set()


def ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)


def q(sql, args=()):
    """Rows as dicts; [] if the table doesn't exist (yet)."""
    try:
        db = ro(); db.row_factory = sqlite3.Row
        try: return [dict(r) for r in db.execute(sql, args)]
        finally: db.close()
    except sqlite3.OperationalError: return []


def lq(sql, args=()):
    """Same as q() but on live.db (read-only)."""
    try:
        db = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True, timeout=5); db.row_factory = sqlite3.Row
        try: return [dict(r) for r in db.execute(sql, args)]
        finally: db.close()
    except sqlite3.OperationalError: return []


def tickmsg(r):
    return {**{k: v for k, v in r.items() if k != "rid"}, "type": "tick", "market_open": market_open(datetime.now(ET))}


def latest_ticks():
    return [tickmsg(r) for r in lq("select rowid rid, * from ticks where rowid in (select max(rowid) from ticks group by ticker)")]


def build():
    """-> DataFrame, one row per (ticker, date) ordered by date, in `row` message shape (None where unknown)."""
    db = ro()
    try:
        d = dataset(db)
        sc = pd.read_sql_query("select ticker, date, p, split from scores", db, parse_dates=["date"])
        ch = pd.read_sql_query("select ticker, date, avg(implied_move) im from chain group by 1, 2", db, parse_dates=["date"])
    finally: db.close()
    d = d.merge(sc, on=["ticker", "date"], how="left").merge(ch, on=["ticker", "date"], how="left")
    x = (d.rv5_rv20.replace([np.inf, -np.inf], np.nan).fillna(1)).round(3)
    o = pd.DataFrame(dict(ticker=d.ticker, date=d.date.dt.strftime("%Y-%m-%d"), x=x,
                          y=d.days_to_next_earnings.fillna(90).clip(0, 90).round(), z=d.vix_close.fillna(20).round(2),
                          label=d.rv_expand, ratio=d.ratio, p=d.p, split=d.split, im=d.im,
                          r2=d.rv20_rv60.replace([np.inf, -np.inf], np.nan), s=d.spy_ret_5d, v=d.vol_z, r20=d.ret_20d,
                          em=d.expected_move_5d, fw=d.fwd_move_5d, bi=d.beat_implied))
    o["_d"] = d.date
    return o


def days(o):
    """One `day` dict per distinct date: stats as-of that date, counting only labels already known (LAG days old)."""
    g = o.assign(l=o.label, t_=(o.split == "test") & (o.p > .5))
    g["th"] = g.t_ * g.l
    a = g.groupby("_d").agg(n=("l", "size"), h=("l", "sum"), mn=("t_", "sum"), mh=("th", "sum"), nbi=("bi", "count"), vix=("z", "mean"))
    c = a[["n", "h", "mn", "mh", "nbi"]].cumsum().shift(LAG).fillna(0)
    out = []
    for i, (dt, r) in enumerate(c.iterrows()):
        out.append({"type": "day", "t": i, "date": dt.strftime("%Y-%m-%d"), "n": int(r.n),
                    "hit": round(r.h / r.n, 3) if r.n else None, "mn": int(r.mn),
                    "mhit": round(r.mh / r.mn, 3) if r.mn else None, "nbi": int(r.nbi), "vix": round(a.vix.iloc[i], 2)})
    return out


def rowmsg(r, t=None):
    m = {k: v for k, v in r.items() if k != "_d" and v is not None and v == v}
    if "label" in m: m["label"] = int(m["label"])
    m["type"] = "row"
    if t is not None: m["t"] = t
    return m


class State:
    """Cached build(); rebuilt when a polled row isn't in it (new data landed)."""
    rows, day = {}, None

    def load(self):
        o = build()
        self.rows = {(r["ticker"], r["date"]): r for r in o.to_dict("records")}
        dd = days(o); self.day = dd[-1] if dd else None
        self.recent = [rowmsg(r) for r in o.tail(300).to_dict("records")]


S = State()


def rows(table, after):
    return q(f"select rowid rid, ticker, date from {table} where rowid>? order by rowid limit 500", (after,))


async def send(msgs):
    for ws in list(clients):
        try:
            for m in msgs: await ws.send_text(json.dumps(m))
        except Exception: clients.discard(ws)


@app.on_event("startup")
async def start():
    async def poll():
        loop = asyncio.get_running_loop()
        try: await loop.run_in_executor(None, S.load)
        except Exception as e: print("initial load failed:", repr(e))
        last = {t: (q(f"select max(rowid) m from {t}") or [{}])[0].get("m") or 0 for t in ("scores", "labels")}
        lt = (lq("select max(rowid) m from ticks") or [{}])[0].get("m") or 0
        while True:
            await asyncio.sleep(5)
            mx = (lq("select max(rowid) m from ticks") or [{}])[0].get("m") or 0
            if mx < lt: lt = mx  # live.db was recreated
            new = lq("select rowid rid, * from ticks where rowid>? order by rowid limit 500", (lt,))
            if new:
                lt = new[-1]["rid"]
                if clients: await send([tickmsg(r) for r in new])
            out, miss = [], False
            for t in last:
                mx = (q(f"select max(rowid) m from {t}") or [{}])[0].get("m") or 0
                if mx < last[t]: last[t] = mx  # table was rewritten (train.py / label.py)
                for r in rows(t, last[t]):
                    last[t] = r["rid"]
                    if (r["ticker"], r["date"]) not in S.rows: miss = True
                    out.append((r["ticker"], r["date"]))
            if miss:
                try: await loop.run_in_executor(None, S.load)
                except Exception as e: print("reload failed:", repr(e))
            msgs = [rowmsg(S.rows[k]) for k in out if k in S.rows]
            if msgs and S.day: msgs.append(S.day)
            if msgs and clients: await send(msgs)
    asyncio.create_task(poll())


@app.get("/")
def index(): return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/replay.json")
def replay():
    f = os.path.join(HERE, "static", "replay.json")
    return FileResponse(f) if os.path.exists(f) else Response(status_code=404)


@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    if S.day: await ws.send_text(json.dumps(S.day))
    for m in getattr(S, "recent", []): await ws.send_text(json.dumps(m))
    for m in latest_ticks(): await ws.send_text(json.dumps(m))
    clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8001)), log_level="warning")
