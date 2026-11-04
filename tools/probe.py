import sys, json
sys.path.insert(0, "collector")
import collect as c
tab = [{"name": "PARIVISION", "points": 1661, "rank": 11}, {"name": "Natus Vincere", "points": 1731, "rank": 8}, {"name": "FURIA", "points": 1938, "rank": 2}, {"name": "BETBOOM", "points": 1840, "rank": 5}]
for i in range(30): tab.append({"name": f"x{i}", "points": 2000 - i * 15, "rank": 0})
def bid(n):
    return c.bo3_team_id({"id": "n:" + n, "name": n})
for a, b in ((0, 1), (2, 3)):
    ids = [bid(tab[a]["name"]), bid(tab[b]["name"])]
    hs = [c.bo3_history(x, pages=1) if x else [] for x in ids]
    print(tab[a]["name"], tab[b]["name"], ids, json.dumps(c.vrs_outcomes_valve(tab[a], tab[b], tab, hists=hs, bids=ids), ensure_ascii=False))
