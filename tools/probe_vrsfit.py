import json, sys, glob, os
sys.path.insert(0, "collector")
os.environ.setdefault("STATS_ONLY", "1")
import collect as C
H = json.load(open("tools/hltv_vrs_fc_20261126b.json"))
R = json.load(open("docs/data/ranking.json"))["vrs"]["teams"]
table = [{"name": t["name"], "points": t["points"], "rank": t["rank"], "region": t.get("region")} for t in R]
L = json.load(open("docs/data/matches.json"))
cards = []
for m in L["matches"]:
    if "Round 4" in (m.get("stage") or "") and "Singapore" in (m.get("tournament") or ""):
        p = f"docs/data/match/{m['id']}.json"
        if os.path.exists(p):
            cards.append(json.load(open(p)))
def hk(n):
    n = (n or "").lower()
    for k in H:
        if k != "_" and (k.lower() in n or n in k.lower()): return k
    return "PARIVISION" if "pvision" in n else None
def run(lbl, ew=None, lan=True):
    if ew is not None: C.MAJOR_STAGE_EW[1] = ew
    C._vrs_base_day.clear()
    ew_, el_, n = 0, 0, 0
    out = []
    for d in cards:
        ts = d["teams"]
        rows = [C.vrs_find(t, table) for t in ts]
        if not all(rows): continue
        o = C.vrs_outcomes_live(rows[0], rows[1], table, {"tournament": d["tournament"], "stage": d["stage"], "begin_at": d["begin_at"], "serie": d.get("serie"), "name": d.get("name")}, lan=lan)
        if not o: continue
        for i, t in enumerate(ts):
            k = hk(t["name"]); h = H.get(k)
            if not h: continue
            w = o["win%d" % i][i]["delta"]; l = o["win%d" % (1 - i)][i]["delta"]
            ew_ += abs(w - h[2]); el_ += abs(l - h[4]); n += 1
            out.append(f"{k}:{w:+d}/{h[2]:+d},{l:+d}/{h[4]:+d}")
    print(f"{lbl}: n={n} ошибка побед {ew_/max(n,1):.2f} поражений {el_/max(n,1):.2f} | " + " ".join(out), flush=True)
for e in (0.1, 0.14, 0.17, 0.2, 0.22):
    run(f"ew={e}", ew=e)
