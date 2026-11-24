import sys, json, math, statistics
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
ref = json.load(open("tools/hltv_vrs_rank_20261124.json"))["points"]
L = c.vrs_live()
R = L.compute(math.floor(c._days(c.NOW))); mx = R["max"]
dd = [R["rating"][L.key(n)] - p for n, p in ref.items() if L.key(n) in R["rating"]]
print("CTRL mean", round(statistics.mean(dd), 1), "mae", round(statistics.mean(map(abs, dd)), 1))
H = {"FlyQuest": (447, 79, 195, 151, 21, 1293), "Chinggis Warriors": (441, 78, 177, 168, -46, 1218),
     "BC.Game": (431, 109, 176, 189, -28, 1277), "BIG": (382, 129, 253, 240, 1, 1405), "Luminosity": (404, 116, 246, 211, 14, 1391)}
for n, h in H.items():
    k = L.key(n); f = R["factors"][k]
    print("F", n, [round(400 * f[x] / mx) for x in ("lan", "network", "offered", "collected")], round(R["rating"][k] - R["seed"][k]), round(R["rating"][k]), "| HLTV", h)
print("LP", {k: v.get("lan") for k, v in c.lp_prize_cache.items()})
