import sys, json, glob, math
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None  # без запросов к HLTV
H = {("Ninjas in Pyjamas", "M80"): (15, -17, 24, -12), ("100 Thieves", "Chinggis Warriors"): (8, -25, 30, -5),
     ("Liquid", "BC.Game"): (9, -24, 29, -7), ("PARIVISION", "DENDELE"): (20, -21, 26, -9), ("FaZe", "Wildcard"): (12, -20, 25, -10)}
table = c.vrs_table()
ds = c.vrs_dataset()
L = c.vrs_live()
print("TABLE", len(table), "vrs3 t", (c.hltv_cache.get("vrs3") or {}).get("t"), "DS", ds.get("day"), ds.get("end"), "now", c._days(c.NOW))
for r in table[:45]:
    k = L.key(r["name"])
    print("D", r["rank"], r["name"], r["points"], round(L.base.get(k, 0)), round(L.base.get(k, 0) - r["points"]),
          [(round(t, 1), round(u)) for t, u in L.prizes.get(k, [])][-3:])
import statistics
for t in (ds["end"], ds["end"] + 7, ds["end"] + 14, math.floor(c._days(c.NOW))):
    R = L.compute(t)["rating"]
    dd = [R.get(L.key(r["name"]), 0) - r["points"] for r in table[:40] if L.key(r["name"]) in R]
    dd2 = [R.get(L.key(r["name"]), 0) - r["points"] for r in table[40:100] if L.key(r["name"]) in R]
    print("T", round(t - ds["end"], 1), "top40 mean", round(statistics.mean(dd), 1), "mae", round(statistics.mean(map(abs, dd)), 1), "41-100 mean", round(statistics.mean(dd2), 1))
print("NEW matches", len(L.new), "since", min((m[0] for m in L.new), default=None) if L.new and isinstance(L.new[0], (list, tuple)) else type(L.new[0]) if L.new else None)
for k, v in (L.played or {}).items():
    print("PLAYED", k, len(v))
def run(d, ew=None, both0=False):
    rows = [c.vrs_find(t, table) for t in d["teams"]]
    if not all(rows): return None
    a, b = rows
    brief = {"tournament": d["tournament"], "stage": d.get("stage"), "name": d.get("name"), "serie": d.get("serie")}
    if ew is None and not both0:
        o = c.vrs_outcomes_live(a, b, table, brief)
        return (o["win0"][0]["delta"], o["win1"][0]["delta"], o["win1"][1]["delta"], o["win0"][1]["delta"])
    f = L.forecast(a["name"], b["name"], math.floor(c._days(c.NOW)), ew=ew or 1.0, lan=True, win_prize=(0, 0), both_prize=(0, 0), base=L.base)
    return (round(f["a_win"][0]), round(f["b_win"][0]), round(f["b_win"][1]), round(f["a_win"][1]))
for f in []:
    d = json.load(open(f)); ts = tuple(t["name"] for t in d.get("teams") or [])
    key = next((k for k in H if all(any(x.lower()[:4] in (n or "").lower() or (n or "").lower()[:3] in x.lower() for n in ts) for x in k)), None)
    if not key: continue
    print("M", ts, d.get("status"), "HLTV", H[key], "cur", run(d), *[(e, run(d, e)) for e in (1.0, 0.7, 0.5, 0.4, 0.3)])
