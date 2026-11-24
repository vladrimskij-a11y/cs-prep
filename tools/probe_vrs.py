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

for n in ("BIG","Luminosity","Sangal"):
    k=L.key(n); print("LP", n, [(round(t-L.ds["end"]), round(u)) for t,u in L.prizes.get(k,[])])
for hist in c._bo3_hist.values():
    for m in hist:
        T = m.get("tournament") or {}
        if "Stake Ranked" in (T.get("name") or ""):
            print("TK", sorted(T.keys())[:40]); print("TV", {k: T.get(k) for k in ("slug", "status", "prize_pool", "tier", "end_date")})
            try: print("PL", c.bo3_tour_places(T.get("slug")))
            except Exception as e: print("PLERR", e)
            print("TIERS", c.TIERS); raise SystemExit
