import sys, json
sys.path.insert(0, "collector")
import collect as c
for n in ("PARIVISION", "Natus Vincere"):
    d = c.valve_detail(n)
    print(n, d["seed"], d["collected"], d["network"], d["lan"], "bc", sorted(d["bc"], reverse=True)[:12], "net", sorted(d["net"], reverse=True)[:12], "lan", sorted(d["lanl"], reverse=True)[:12])
    top = lambda v: sum(sorted(v, reverse=True)[:10]) / 10
    avg = (d["offered"] + c.valve_curve(top(d["bc"])) + top(d["net"]) + top(d["lanl"])) / 4
    print("  recomputed seed", 400 + (avg - d["min"]) / (d["max"] - d["min"]) * 1600, "curve", c.valve_curve(top(d["bc"])))
bid = 8171
for m in c.bo3_history(bid, pages=1)[:8]:
    print(m.get("start_date"), c.opp_of(m, bid).get("name"), m.get("winner_team_id") == bid, m.get("tier"))
