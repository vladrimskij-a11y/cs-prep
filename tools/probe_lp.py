import sys
sys.path.insert(0, "collector")
import collect as c
for n in ("Stake Ranked Episode 6", "RUSH:Brno 2026", "CCT Europe 2026 Series 13", "NODWIN Clutch Series 14"):
    print("S", n, c.lp_prize_slots(n), c.lp_prize_cache[n].get("title"))
