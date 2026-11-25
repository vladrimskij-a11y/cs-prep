import sys, json
sys.path.insert(0, "collector")
import collect as c
L = c.d2_recent_matches()
print("feed", len(L), "archive", len(c.d2_stats))
for mt in L[:40]:
    if (mt.get("date") or "") >= "2026-11-24":
        e = c.d2_stats.get(str(mt["id"]))
        print("FEED", mt.get("date"), mt["id"], mt.get("slug"), "in archive:", bool(e), e and e.get("done"), e and e.get("teams"))
for k, e in c.d2_stats.items():
    if (e.get("date") or "") >= "2026-11-24":
        print("ARCH", k, e.get("date"), e.get("teams"), e.get("done"))
print(c.d2_find_stats(["Ninjas in Pyjamas", "Liquid"], "2026-11-25T10:30:00Z")[1])
print(c.d2_find_stats(["BC.Game", "Chinggis Warriors"], "2026-11-25T10:10:00Z")[1])
h = c.d2_get("/results")
import re
print("results html", len(h or ""), re.findall(r'href="/matches/(\d+)/([^"]+)"', h or "")[:8])
