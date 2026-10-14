"""Serve the dashboard and tail orb.db to websocket clients (read-only; never locks the collector)."""
import asyncio, json, os, sqlite3, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

HERE = os.path.dirname(__file__)
DB = os.environ.get("ORB_DB") or os.path.join(HERE, "orb.db")
LAB = {"timeout": "to"}
app = FastAPI()
clients = set()


def q(sql, args=()):
    """Rows as dicts; [] if the table doesn't exist (yet)."""
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=0.5)
        db.row_factory = sqlite3.Row
        try: return [dict(r) for r in db.execute(sql, args)]
        finally: db.close()
    except sqlite3.OperationalError: return []


def launch_msg(r):
    return {"type": "launch", "mint": r["mint"], "symbol": r["symbol"], "name": r["name"],
            "creator": r["creator"], "mcap_sol": r["mcap_sol"], "ts": r["ts"]}


def stats():
    now = time.time()
    one = lambda s, a=(): next(iter((q(s, a) or [{"n": 0}])[0].values()), 0) or 0
    lab = {r["label"]: r["n"] for r in q("select label, count(*) n from labels group by label")}
    return {"type": "stats", "launches_24h": one("select count(*) from launches where ts>?", (now - 86400,)),
            "blocked": one("select count(*) from guard where blocked"),
            "labels": {k: sum(v for l, v in lab.items() if LAB.get(l, l) == k) for k in ("tp", "sl", "to")},
            "launch_rate_per_min": one("select count(*) from launches where ts>?", (now - 300,)) / 5,
            # ponytail: net buy-sell SOL over 5 min; needs trades (PUMPPORTAL_KEY) else 0
            "paper": (q("select count(*) n, avg(pnl_sol > 0) win_rate, sum(pnl_sol) total_pnl_sol from positions") or [{}])[0],
            "guard": (q("select avg(bundled_share) bundled_share_mean, avg(blocked) blocked_rate, "
                        "avg(creator_prior_launches >= 3) serial_creator_rate from guard g join launches l using(mint) "
                        "where l.ts>?", (now - 1800,)) or [{}])[0],
            "graduated_1h": one("select count(*) from (select mint from trades where ts>? group by mint "
                                "having max(mcap_sol) >= 300)", (now - 3600,)),
            "sol_inflow_per_min": one("select sum(case side when 'buy' then sol else -sol end) from trades where ts>?", (now - 300,)) / 5}


def rows(table, after):
    return q(f"select rowid rid, * from {table} where rowid>? order by rowid limit 500", (after,))


MSG = {  # table -> row -> message
    "launches": launch_msg,
    "trades": lambda r: {"type": "trade", "mint": r["mint"], "side": r["side"], "sol": r["sol"],
                         "price": r["v_sol"] / r["v_tokens"] if r["v_tokens"] else None, "ts": r["ts"]},
    "labels": lambda r: {"type": "label", "mint": r["mint"], "entry_s": r["entry_s"], "label": LAB.get(r["label"], r["label"])},
    "guard": lambda r: {"type": "guard", "mint": r["mint"], "blocked": bool(r["blocked"]), "reason": r["reason"],
                        "bundled_share": r.get("bundled_share")},
    "scores": lambda r: {"type": "score", "mint": r["mint"], "p_tp": r["p_tp"], "p_sl": r["p_sl"], "p_to": r["p_to"],
                         "split": r["split"]},
    "positions": lambda r: {"type": "position", "mint": r["mint"], "reason": r["reason"], "pnl_pct": r["pnl_pct"],
                            "pnl_sol": r["pnl_sol"]},
}


async def send(msgs):
    for ws in list(clients):
        try:
            for m in msgs: await ws.send_text(json.dumps(m))
        except Exception: clients.discard(ws)


@app.on_event("startup")
async def start():
    async def poll():
        last = {t: (q(f"select max(rowid) m from {t}") or [{}])[0].get("m") or 0 for t in MSG}
        n = 0
        while True:
            await asyncio.sleep(1)
            out = []
            for t, f in MSG.items():
                for r in rows(t, last[t]):
                    last[t] = r["rid"]; out.append(f(r))
            if (n := n + 1) % 2 == 0: out.append(stats())
            if out and clients: await send(out)
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
    await ws.send_text(json.dumps(stats()))
    for r in reversed(q("select * from launches order by rowid desc limit 20")):
        await ws.send_text(json.dumps(launch_msg(r)))
    clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
