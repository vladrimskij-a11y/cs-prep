import sys, json
sys.path.insert(0, "collector")
import collect as c
for a, b in (("Liquid", "Wildcard"), ("FaZe", "BC.Game"), ("Eternal Fire", "DENDELE")):
    ia, ib = c.team_by_name(a), c.team_by_name(b)
    res = c.bo3("/matches", {"page[limit]": 20, "sort": "start_date", "filter[matches.status][in]": "upcoming,current", "filter[matches.team_ids][overlap]": ia}).get("results") or []
    m = next((x for x in res if {x.get("team1_id"), x.get("team2_id")} == {ia, ib}), None)
    print("M", a, b, ia, ib, bool(m), m and m.get("start_date"), json.dumps((m or {}).get("bet_updates"))[:400])
    print("   other", [(x.get("team1_id"), x.get("team2_id"), x.get("start_date")) for x in res[:5]])
