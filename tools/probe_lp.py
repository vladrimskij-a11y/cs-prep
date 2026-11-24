import sys, json, glob, statistics
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
ref = json.load(open("tools/hltv_vrs_rank_20261124.json"))["points"]
table = c.vrs_table()
L = c.vrs_live(); R = L.base
dd = [R[L.key(n)] - p for n, p in ref.items() if L.key(n) in R]
print("CTRL mean", round(statistics.mean(dd), 1), "mae", round(statistics.mean(map(abs, dd)), 1))
for n in ("BIG", "Luminosity", "Sangal", "GamerLegion", "Inner Circle", "Eternal Fire", "FlyQuest"):
    print("B", n, ref[n], round(R.get(L.key(n), 0) - ref[n]))
H = {("Eternal Fire", "FlyQuest"): (9, -21, 27, -9), ("100 Thieves", "Chinggis Warriors"): (8, -25, 30, -5), ("FaZe", "Wildcard"): (12, -20, 25, -10)}
for f in glob.glob("docs/data/match/*.json"):
    d = json.load(open(f)); ts = tuple(t["name"] for t in d.get("teams") or [])
    key = next((k for k in H if set(k) == set(ts)), None)
    if not key: continue
    rows = [c.vrs_find(t, table) for t in d["teams"]]
    o = c.vrs_outcomes_live(rows[0], rows[1], table, {"tournament": d["tournament"], "stage": d.get("stage"), "name": d.get("name"), "serie": d.get("serie")})
    print("M", ts, "HLTV", H[key], "our", (o["win0"][0]["delta"], o["win1"][0]["delta"], o["win1"][1]["delta"], o["win0"][1]["delta"]))
