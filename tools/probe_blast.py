import os, sys, json
sys.path.insert(0, "collector")
import collect as c
j = c.bo3("/tournaments", {"page[limit]": 50, "sort": "start_date", "filter[tournaments.status][in]": "upcoming"})
for t in j.get("results") or []:
    print("BO3", t.get("id"), t.get("start_date"), t.get("tier"), t.get("status"), t.get("name"), t.get("slug"))
for page in (1, 2):
    b = c.panda("/csgo/tournaments/upcoming", {"per_page": 100, "page": page, "sort": "begin_at"})
    for t in b:
        n = ((t.get("league") or {}).get("name") or "") + " " + ((t.get("serie") or {}).get("full_name") or "")
        if "2027" in n or "BLAST" in n.upper():
            print("PS", t.get("begin_at"), t.get("tier"), n, t.get("name"))
