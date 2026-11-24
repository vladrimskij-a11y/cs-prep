import sys, collections
sys.path.insert(0, "collector")
import collect as c
seen = {}
for bid in (c.team_by_name("BIG"), c.team_by_name("BC.Game"), c.team_by_name("FlyQuest"), c.team_by_name("Vitality")):
    for m in c.bo3_history(bid, pages=1):
        T = m.get("tournament") or {}
        seen[T.get("name")] = (T.get("event_level"), T.get("event_scope"), T.get("tier"), T.get("tier_rank"))
for k, v in seen.items(): print("EV", k, v)
