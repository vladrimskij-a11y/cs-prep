import sys, json, glob, math
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
c._hltv_blocked[0] = True
H = json.load(open("tools/hltv_vrs_fc_20261124.json"))
L = c.vrs_live()
table = c.vrs_table()
def find(n):
    r = c.vrs_find({"name": n}, table); return r
base_md = {}
for f in sorted(glob.glob("docs/data/match/*.json")):
    d = json.load(open(f))
    if len(d.get("teams") or []) != 2:
        continue
    names = [t.get("_alt") if c.norm(t["name"]) in c.TEAM_TEMP_NAMES else t["name"] for t in d["teams"]]
    rows = [find(n) for n in names]
    if not all(rows):
        print("no rows", names); continue
    hk = [next((k for k in H if c.norm(k) in (c.norm(n), c.norm(r["name"]))), None) for n, r in zip(names, rows)]
    tn = d.get("tournament") or ""
    ms = c.major_stage(tn, d.get("stage"))
    ew = c.MAJOR_STAGE_EW.get(ms, 0.624)
    today = math.floor(c._days(c.NOW)); mday = math.floor(c._days(d["begin_at"]))
    if mday not in base_md:
        base_md[mday] = L.compute(mday)["rating"]
    for lab, now, base in (("A today", today, L.base), ("B mday/base-today", mday, L.base), ("C mday/base-mday", mday, base_md[mday])):
        fc = L.forecast(rows[0]["name"], rows[1]["name"], now, ew=ew, lan=True, base=base)
        if not fc: print("nofc", names); break
        out = []
        for i in (0, 1):
            w = round(fc["a_win" if i == 0 else "b_win"][i]); l = round(fc["b_win" if i == 0 else "a_win"][i])
            h = H.get(hk[i]) or [None] * 6
            out.append(f"{names[i]} W{w:+d}/{h[2]} L{l:+d}/{h[4]}")
        print(lab.ljust(20), " | ".join(out))
    print("cur", [r["points"] for r in rows], [ (H.get(k) or [None])[0] for k in hk])
