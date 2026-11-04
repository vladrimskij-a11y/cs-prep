import sys, json
sys.path.insert(0, "collector")
import collect as c
tab = [{"name": "PARIVISION", "points": 1661, "rank": 11}, {"name": "Natus Vincere", "points": 1731, "rank": 8}]
for i in range(30): tab.append({"name": f"x{i}", "points": 2000 - i * 15, "rank": 0})
ids = [8171, 787]
hs = [c.bo3_history(x, pages=1) for x in ids]
print(json.dumps(c.vrs_outcomes_valve(tab[0], tab[1], tab, hists=hs, bids=ids), ensure_ascii=False))
