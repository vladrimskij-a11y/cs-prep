import sys, json, statistics
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
ref = json.load(open("tools/hltv_vrs_rank_20261124.json"))["points"]
L = c.vrs_live()
R = L.base
dd = []
for n, p in ref.items():
    k = L.key(n)
    if k in R:
        d = R[k] - p; dd.append(d); print("D", n, p, round(R[k]), round(d))
    else:
        print("D", n, p, "нет")
print("CTRL n", len(dd), "mean", round(statistics.mean(dd), 1), "mae", round(statistics.mean(map(abs, dd)), 1))
for n in ("BIG", "Luminosity", "Sangal", "HEROIC", "SINNERS", "GamerLegion", "Envy", "BBL"):
    k = L.key(n)
    ms = [m for m in L.new if k in (m.get("w"), m.get("l"))]
    print("NEW", n, len(ms), sorted(round(m["t"] - L.ds["end"], 1) for m in ms)[-12:])

seen = {}
for hist in c._bo3_hist.values():
    for m in hist:
        t = c._days(m.get("start_date"))
        if t is None or t < L.ds["end"]: continue
        T = m.get("tournament") or {}
        a, b = (m.get("team1") or {}).get("name"), (m.get("team2") or {}).get("name")
        if any(x in (a, b) for x in ("BIG", "Luminosity", "Sangal", "SINNERS", "GamerLegion", "Envy", "BBL", "Virtus.pro", "BC.Game", "HOTU")):
            key = T.get("name")
            seen.setdefault(key, [T.get("tier"), T.get("prize_pool") or T.get("prize"), T.get("status"), set()])[3].update([a, b])
for k, v in seen.items(): print("TOUR", k, v[0], v[1], v[2], sorted(x for x in v[3] if x)[:12])
for n in ("BIG", "Luminosity", "Sangal"):
    k = L.key(n); print("PRZ", n, [(round(t - L.ds["end"]), round(u)) for t, u in (L.ds["teams"][k]["prizes"] if k in L.ds["teams"] else [])][-8:])
