"""Export the last 90 min of orb.db as static/replay.json (read-only). Messages reuse serve.py's row->message fns."""
import json, os, sqlite3, time
from serve import MSG, DB

WIN, CAP = 5400, 4500  # launches from the hour before the window are included (clamped to t=0) so their labels land inside it
end = time.time(); start = end - WIN
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30); db.row_factory = sqlite3.Row
Q = lambda s, a=(): [dict(r) for r in db.execute(s, a)]
one = lambda s, a=(): (db.execute(s, a).fetchone() or [0])[0] or 0

L = Q("select * from (select * from launches where ts>=? and ts<=? order by ts desc limit ?) order by ts", (start - 3600, end, CAP))
ts = {r["mint"]: r["ts"] for r in L}
ev = []
clamp = lambda x: min(max(x, start), end)
def add(at, m): ev.append(dict(m, at=round(clamp(at), 3)))
for r in L: add(r["ts"], MSG["launches"](r))
for r in Q("select * from guard"):
    if r["mint"] in ts: add(ts[r["mint"]] + 15, MSG["guard"](r))
for r in Q("select * from scores"):  # scores has no ts column -> launch ts+16
    if r["mint"] in ts: add(ts[r["mint"]] + 16, MSG["scores"](r))
for r in Q("select * from labels where entry_s=15"):
    if r["mint"] in ts: add(ts[r["mint"]] + 3600, MSG["labels"](r))
for r in Q("select * from positions"):
    if r["mint"] in ts: add(r["exit_ts"] or ts[r["mint"]] + 16, MSG["positions"](r))

LAB = {"timeout": "to"}
def stats(now):  # serve.stats() SQL, evaluated as-of `now`; labels/guard use the replay's event times
    lab = {}
    for k, n in db.execute("select label, count(*) from labels join launches using(mint) where entry_s=15 and ts+3600<=? group by label", (now,)):
        lab[LAB.get(k, k)] = lab.get(LAB.get(k, k), 0) + n
    pos = db.execute("select count(*) n, avg(pnl_sol > 0) win_rate, sum(pnl_sol) total_pnl_sol from positions where exit_ts<=?", (now,)).fetchone()
    g = db.execute("select avg(bundled_share), avg(blocked), avg(creator_prior_launches >= 3) from guard g join launches l using(mint) where l.ts>? and l.ts+15<=?", (now - 1800, now)).fetchone()
    return {"type": "stats", "at": round(now, 3),
            "launches_24h": one("select count(*) from launches where ts>? and ts<=?", (now - 86400, now)),
            "blocked": one("select count(*) from guard join launches using(mint) where blocked and ts+15<=?", (now,)),
            "labels": {k: lab.get(k, 0) for k in ("tp", "sl", "to")},
            "launch_rate_per_min": one("select count(*) from launches where ts>? and ts<=?", (now - 300, now)) / 5,
            "paper": dict(pos),
            "guard": {"bundled_share_mean": g[0], "blocked_rate": g[1], "serial_creator_rate": g[2]},
            "graduated_1h": one("select count(*) from (select mint from trades where ts>? and ts<=? group by mint having max(mcap_sol) >= 300)", (now - 3600, now)),
            "sol_inflow_per_min": one("select sum(case side when 'buy' then sol else -sol end) from trades where ts>? and ts<=?", (now - 300, now)) / 5}

first = start
for k in range(int((end - first) // 60) + 1): ev.append(stats(first + 60 * k))
ev.sort(key=lambda e: e["at"])
t0 = ev[0]["at"]
for e in ev: e["t"] = round(e["at"] - t0, 3)
out = os.path.join(os.path.dirname(__file__), "static", "replay.json")
json.dump(ev, open(out, "w"), separators=(",", ":"))
c = {}
for e in ev: c[e["type"]] = c.get(e["type"], 0) + 1
print(len(ev), "events", c, os.path.getsize(out) // 1024, "KB")
