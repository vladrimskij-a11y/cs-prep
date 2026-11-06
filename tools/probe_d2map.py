import json, re, sys, time, urllib.request
sys.path.insert(0, "collector")
import collect as C
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(p):
    time.sleep(1.2)
    r = urllib.request.Request("https://www.dust2.us" + p, headers={"User-Agent": UA, "Accept": "text/html"})
    return re.sub(r"\s+", " ", urllib.request.urlopen(r, timeout=40).read().decode("utf-8", "replace"))
for p, teams in (("/matches/2400006/parivision-vs-natus-vincere", [{"name": "Natus Vincere", "acronym": "NAVI"}, {"name": "PARIVISION", "acronym": "PRV"}]),
                 ("/matches/2400007/furia-vs-betboom", [{"name": "FURIA", "acronym": "FURIA"}, {"name": "BetBoom Team", "acronym": "BB"}])):
    R = C.d2_parse_result(get(p)); R["id"] = p.split("/")[2]
    print(p, json.dumps(R["maps"]), {k: {t: len(v) for t, v in x.items()} for k, x in R["stats"].items()})
    print(" first rows", {k: list(x.values())[0][:2] for k, x in R["stats"].items()})
    d = {"teams": teams, "d2_res": R, "begin_at": "2026-11-05T10:00:00Z", "bo": 3, "tournament": "IEM"}
    print(" fill", C.d2_fill_series(d), d.get("cur_sid"))
    S = (d.get("series") or {}).get(d.get("cur_sid")) or {}
    print(" series", json.dumps({"teams": S.get("teams"), "maps": [{k: g[k] for k in ("map", "score", "winner")} | {"pl": [len(x) for x in g["players"]] if g.get("players") else None} for g in S.get("maps", [])], "players": bool(S.get("players"))}))
