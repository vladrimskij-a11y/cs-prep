import sys, json, glob, math, statistics
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
H = {("Ninjas in Pyjamas", "M80"): (15, -17, 24, -12), ("100 Thieves", "Chinggis Warriors"): (8, -25, 30, -5),
     ("Liquid", "BC.Game"): (9, -24, 29, -7), ("PARIVISION", "DENDELE"): (20, -21, 26, -9), ("FaZe", "Wildcard"): (12, -20, 25, -10)}
table = c.vrs_table()
L = c.vrs_live()
R = L.base
dd = [R.get(L.key(r["name"]), 0) - r["points"] for r in table[:40] if L.key(r["name"]) in R]
print("CTRL top40 mean", round(statistics.mean(dd), 1), "mae", round(statistics.mean(map(abs, dd)), 1))
for r in table[:45]:
    k = L.key(r["name"]); print("D", r["rank"], r["name"], r["points"], round(R.get(k, 0) - r["points"]))
for f in glob.glob("docs/data/match/*.json"):
    d = json.load(open(f)); ts = tuple(t["name"] for t in d.get("teams") or [])
    key = next((k for k in H if all(any(x.lower()[:4] in (n or "").lower() or (n or "").lower()[:3] in x.lower() for n in ts) for x in k)), None)
    if not key: continue
    rows = [c.vrs_find(t, table) for t in d["teams"]]
    if not all(rows): print("M", ts, "no rows"); continue
    o = c.vrs_outcomes_live(rows[0], rows[1], table, {"tournament": d["tournament"], "stage": d.get("stage"), "name": d.get("name"), "serie": d.get("serie")})
    print("M", ts, d.get("stage"), "HLTV", H[key], "our", (o["win0"][0]["delta"], o["win1"][0]["delta"], o["win1"][1]["delta"], o["win0"][1]["delta"]))
