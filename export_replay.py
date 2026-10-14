"""Write static/replay.json from vol.db (read-only): per trading day one `day` event then that day's `row` events.
t = index of the date, so playback is one trading day per tick. Rows omit date/t/type (the preceding `day` carries them)
and split unless 'test'; the client restores them. Thins pre-2015 days if the file would exceed 4 MB."""
import json, os
from serve import build, days, rowmsg

MAX = 4_000_000
HERE = os.path.dirname(os.path.abspath(__file__))
o = build()
dd = days(o)
tix = {d["date"]: d["t"] for d in dd}
src = {}
for r in o.to_dict("records"):
    t = tix[r["date"]]
    m = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in rowmsg(r).items() if k not in ("date", "type")}
    if m.get("split") != "test": m.pop("split", None)
    src.setdefault(t, []).append(m)


def dump(step):
    # ponytail: pre-2015 days (and their day events) thinned to 1 in `step`; post-2015 (incl. the test period) always complete
    ev = []
    for d in dd:
        if d["date"] >= "2015" or d["t"] % step == 0: ev.append(d); ev += src.get(d["t"], [])
    return json.dumps(ev, separators=(",", ":")), ev


step = 1
s, ev = dump(step)
while len(s) > MAX and step < 50: step += 1; s, ev = dump(step)
out = os.path.join(HERE, "static", "replay.json")
open(out, "w").write(s)
c = {"day": 0, "row": 0}
for e in ev: c["row" if "ticker" in e else "day"] += 1
print(len(ev), "events", c, os.path.getsize(out) // 1024, "KB", f"pre-2015 thinned 1/{step}" if step > 1 else "")
