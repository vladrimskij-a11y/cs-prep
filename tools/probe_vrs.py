import sys, re
sys.path.insert(0, "collector")
import collect as c
for n in ("Aurora", "Alliance", "paiN", "Inner Circle", "PRIVATE", "Fluxo", "Nexus"):
    names = c.lp_names({"name": n})
    pg = c.lp_pages(names)
    hit = [(k, len(v), "Squad|status=active" in v) for k, v in pg.items()]
    s = c.lp_search(n)
    print("T", n, names, hit, "search:", s)
    txt = next((v for v in pg.values() if v), None) or (c.lp_pages([s]).get(s) if s else "")
    i = (txt or "").find("Squad")
    print("S", n, (txt or "")[i:i + 900].replace("\n", " ⏎ ") if i >= 0 else (txt or "")[:300].replace("\n", " ⏎ "))
