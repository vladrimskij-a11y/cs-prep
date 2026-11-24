import sys, json
sys.path.insert(0, "collector")
import collect as c
ia = c.team_by_name("Liquid")
for prm in ({"filter[matches.team_ids][overlap]": ia}, {"filter[matches.team1_id][eq]": ia}, {"filter[matches.status][in]": "upcoming,current"}):
    try:
        r = c.bo3("/matches", {"page[limit]": 5, "sort": "-start_date", **prm})
        res = r.get("results") or []
        print("P", prm, r.get("total"), [(x.get("team1_id"), x.get("team2_id"), x.get("status"), x.get("start_date"), bool(x.get("bet_updates"))) for x in res])
    except Exception as e:
        print("P", prm, "ERR", e)
