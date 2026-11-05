import json, re, sys
sys.path.insert(0, "collector")
import collect as c
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            p = path + "." + k
            if re.search(r"rank|vrs|valve|point|rating", k, re.I):
                print("  ", p, "=", json.dumps(v)[:200])
            walk(v, p)
    elif isinstance(o, list):
        for i, v in enumerate(o[:2]):
            walk(v, path + f"[{i}]")
for nm in ("PARIVISION", "Natus Vincere"):
    bid = c.bo3_team_id({"id": "x" + nm, "name": nm})
    print("==", nm, bid)
    for path in (f"/teams/{bid}", ):
        try:
            j = c.bo3(path); print(path, "ключи:", list(j.keys())[:60] if isinstance(j, dict) else type(j)); walk(j)
        except Exception as e:
            print(path, e)
for path, params in (("/rankings", None), ("/rankings/vrs", None), ("/teams/rankings", None), ("/valve_rankings", None), ("/vrs", None),
                     ("/teams", {"sort": "rank", "page[limit]": 3}), ("/rankings", {"type": "valve"})):
    try:
        j = c.bo3(path, params); print("OK", path, params, json.dumps(j)[:600])
    except Exception as e:
        print("нет", path, params, repr(e)[:80])
