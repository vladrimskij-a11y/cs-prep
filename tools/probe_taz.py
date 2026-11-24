import sys, json
sys.path.insert(0, "collector")
import collect as c
for n in ("TaZ", "Xizt", "zonic", "sycrone", "Maddened", "B1ad3", "kassad"):
    print(n, "player:", c.nick_find(n, c.hltv_player_ids) and c.hltv_player_ids[c.nick_find(n, c.hltv_player_ids)], "coach:", c.nick_find(n, c.hltv_coach_ids) and c.hltv_coach_ids[c.nick_find(n, c.hltv_coach_ids)])
print("players", len(c.hltv_player_ids), "coaches", len(c.hltv_coach_ids))
