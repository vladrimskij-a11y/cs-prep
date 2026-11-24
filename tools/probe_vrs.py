import sys, json, math
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
L = c.vrs_live()
R = L.compute(math.floor(c._days(c.NOW)))
mx = R["max"]
H = {"FlyQuest": (447, 79, 195, 151, 21, 1293), "Chinggis Warriors": (441, 78, 177, 168, -46, 1218),
     "BC.Game": (431, 109, 176, 189, -28, 1277), "BIG": (382, 129, 253, 240, 1, 1405), "Luminosity": (404, 116, 246, 211, 14, 1391)}
for n, h in H.items():
    k = L.key(n); f = R["factors"].get(k)
    if not f: print("NO", n); continue
    pts = [round(400 * f[x] / mx) for x in ("lan", "network", "offered", "collected")]
    h2h = round(R["rating"][k] - R["seed"][k])
    print("F", n, "ours lan/net/off/col/h2h/total", pts, h2h, round(R["rating"][k]), "| HLTV", h)
    wins = [m for m in L.ds["matches"] + L.new if m.get("w") == k]
    A = lambda t: c.VM.age(t, math.floor(c._days(c.NOW)))
    now_ = math.floor(c._days(c.NOW))
    nt = sorted([(round(R["own"].get(m["l"], 0) * A(m["t"]) * m["ew"], 3), round(m["t"] - L.ds["end"]), L.name(m["l"]), round(m["ew"], 2)) for m in wins], reverse=True)[:10]
    print("NET", n, nt)
    bc = sorted([(round(R["off"].get(m["l"], 0) * A(m["t"]) * m["ew"], 3), round(m["t"] - L.ds["end"]), L.name(m["l"]), round(m["ew"], 2)) for m in wins], reverse=True)[:10]
    print("BC", n, bc)
    print("PZ", n, sorted([(round(t - L.ds["end"]), round(u)) for t, u in (L.ds["teams"][k]["prizes"] + L.prizes.get(k, []))], reverse=True)[:10])
    lanw = sorted([(round(m["t"] - L.ds["end"]), round(A(m["t"]), 2), L.name(m["l"])) for m in wins if m.get("lan")], reverse=True)[:12]
    print("LAN", n, lanw)
    print("REF", R["refs"], "max", round(mx, 4))
