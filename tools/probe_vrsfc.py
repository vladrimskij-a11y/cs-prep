import sys, json, glob, math, re
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
c._hltv_blocked[0] = True
H = json.load(open("tools/hltv_vrs_fc_20261124.json"))
L = c.vrs_live()
table = c.vrs_table()
res = {}
BM = {}
for f in sorted(glob.glob("docs/data/match/*.json")):
    d = json.load(open(f))
    if len(d.get("teams") or []) != 2:
        continue
    names = [t.get("_alt") if c.norm(t["name"]) in c.TEAM_TEMP_NAMES else t["name"] for t in d["teams"]]
    rows = [c.vrs_find({"name": n}, table) for n in names]
    if not all(rows):
        continue
    hk = [next((k for k in H if c.norm(k) in (c.norm(n), c.norm(r["name"]))), None) for n, r in zip(names, rows)]
    mday = math.floor(c._days(d["begin_at"]))
    if mday not in BM:
        BM[mday] = L.compute(mday)["rating"]
    for ew in (0.3, 0.35, 0.4):
        for lan in ("B", "C"):
            fc = L.forecast(rows[0]["name"], rows[1]["name"], mday, ew=ew, lan=True, base=L.base if lan == "B" else BM[mday])
            for i in (0, 1):
                w = fc["a_win" if i == 0 else "b_win"][i]; l = fc["b_win" if i == 0 else "a_win"][i]
                h = H.get(hk[i])
                if h:
                    res.setdefault((ew, lan), []).append((w - h[2], l - h[4]))
for k, v in sorted(res.items()):
    mw = sum(abs(x) for x, _ in v) / len(v); ml = sum(abs(y) for _, y in v) / len(v)
    bw = sum(x for x, _ in v) / len(v); bl = sum(y for _, y in v) / len(v)
    print("EW", k, f"win MAE {mw:.2f} bias {bw:+.2f} | loss MAE {ml:.2f} bias {bl:+.2f}")
