import sys, json, glob, math
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None  # без запросов к HLTV
H = {  # HLTV 24.11: (a, b): aw, al, bw, bl
    ("Ninjas in Pyjamas", "M80"): (15, -17, 24, -12), ("100 Thieves", "Chinggis Warriors"): (8, -25, 30, -5),
    ("Liquid", "BC.Game"): (9, -24, 29, -7), ("PARIVISION", "DENDELE"): (20, -21, 26, -9), ("FaZe", "Wildcard"): (12, -20, 25, -10)}
table = c.vrs_table()
L = c.vrs_live()
print("TABLE", len(table), "L", bool(L))
ex = (c.hltv_cache.get("event3:PGL Major Singapore 2026") or {}).get("data") or {}
print("EV prize", ex.get("prize"), "places", json.dumps(ex.get("places"))[:900])
for k in c.hltv_cache:
    if k.startswith("event3:") and "ingapore" in k: print("KEY", k, ((c.hltv_cache[k].get("data") or {}).get("prize")))
print("played", {k: len(v) for k, v in (L.played or {}).items() if "ingapore" in k})
def run(d, both0=False, ewf=None):
    rows = [c.vrs_find(t, table) for t in d["teams"]]
    if not all(rows): return None
    brief = {"tournament": d["tournament"], "stage": d.get("stage"), "name": d.get("name"), "serie": d.get("serie")}
    a, b = rows
    if both0 or ewf:
        tn = brief["tournament"]; ex = (c.hltv_cache.get("event3:" + tn) or {}).get("data") or {}
        pool = ex.get("prize") or 0
        ew = ewf if ewf else (c.VM.curve(min(max(pool, 1.0), 1e6) / 1e6) if pool else 0.5)
        f = L.forecast(a["name"], b["name"], math.floor(c._days(c.NOW)), ew=ew, lan=True, win_prize=(0, 0), both_prize=(0, 0), base=L.base)
        return (round(f["a_win"][0]), round(f["b_win"][0]), round(f["b_win"][1]), round(f["a_win"][1]))
    o = c.vrs_outcomes_live(a, b, table, brief)
    return (o["win0"][0]["delta"], o["win1"][0]["delta"], o["win1"][1]["delta"], o["win0"][1]["delta"])
for f in glob.glob("docs/data/match/*.json"):
    d = json.load(open(f)); ts = tuple(t["name"] for t in d.get("teams") or [])
    key = next((k for k in H if all(any(x.lower()[:5] in (n or "").lower() or (n or "").lower()[:5] in x.lower() for n in ts) for x in k)), None)
    if not key: continue
    print("M", ts, "HLTV", H[key], "cur", run(d), "both0", run(d, True), "ew1", run(d, True, 1.0), "ew.5", run(d, True, 0.5))
