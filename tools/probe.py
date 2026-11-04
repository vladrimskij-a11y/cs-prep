import sys, json
sys.path.insert(0, "collector")
import collect as c
tab = [{"name": "PARIVISION", "points": 1661, "rank": 11}, {"name": "Natus Vincere", "points": 1731, "rank": 8}, {"name": "FURIA", "points": 1938, "rank": 2}, {"name": "BETBOOM", "points": 1840, "rank": 5}]
for i in range(30): tab.append({"name": f"x{i}", "points": 2000 - i * 15, "rank": 0})
for a, b in ((0, 1), (2, 3)):
    print(tab[a]["name"], tab[b]["name"], json.dumps(c.vrs_outcomes_valve(tab[a], tab[b], tab), ensure_ascii=False))
d = c.valve_detail("PARIVISION"); print({k: v for k, v in (d or {}).items() if k not in ("bc", "net", "lanl")})
