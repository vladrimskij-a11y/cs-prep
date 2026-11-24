import sys, re, json, time, urllib.parse
sys.path.insert(0, "collector")
import collect as c
def srch(q):
    time.sleep(3)
    u = c.LP_API + "?" + urllib.parse.urlencode({"action": "opensearch", "search": q, "limit": 8, "format": "json"})
    return json.loads(c.http_get(u, {"User-Agent": c.LP_UA, "Accept": "application/json"}))[1]
for q in ("RUSH", "CCT Europe Series 13", "NODWIN Clutch Series"):
    r = srch(q); print("Q", q, "->", r)
r = srch("Stake Ranked Episode 6") or srch("Stake Ranked")
t = next((x for x in r if "6" in x), r[0] if r else None)
if t:
    pg = c.lp_pages([t]).get(t, "")
    print("T", t, "LEN", len(pg))
    i = pg.lower().find("{{prize pool")
    if i < 0: i = pg.lower().find("prizepool|")
    if i < 0: i = pg.lower().find("slot|")
    print("PP", pg[i:i+2500].replace("\n", " ⏎ ") if i >= 0 else pg[:800].replace("\n", " ⏎ "))
