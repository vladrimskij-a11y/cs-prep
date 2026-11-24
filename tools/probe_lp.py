import sys, re
sys.path.insert(0, "collector")
import collect as c
for q in ("Stake Ranked Episode 6", "RUSH Brno 2026", "CCT Europe 2026 Series 13", "NODWIN Clutch Series 14"):
    t = c.lp_search(q)
    print("Q", q, "->", t)
    if not t: continue
    pg = c.lp_pages([t]).get(t, "")
    print("LEN", len(pg))
    i = pg.lower().find("prize pool start")
    print("PP", pg[i:i+1800].replace("\n", " ⏎ ") if i >= 0 else pg[:600].replace("\n", " ⏎ "))
