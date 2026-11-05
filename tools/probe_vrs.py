import re, sys
sys.path.insert(0, "collector")
import collect as c
ups = c.d2_upcoming()
print("матчей:", len(ups))
m = next((x for x in ups if "parivision" in x["slug"] and ("natus" in x["slug"] or "navi" in x["slug"])), None) or (ups[0] if ups else None)
print(m)
if m:
    h = c.d2_get(f"/matches/{m['id']}/{m['slug']}") or ""
    b = re.sub(r"\s+", " ", h)
    print("длина", len(b))
    for kw in ("VRS", "vrs", "forecast", "Forecast", "TEAM WINS", "Team wins", "pt<", "CURRENT"):
        for mm in list(re.finditer(re.escape(kw), b))[:3]:
            print("==", kw, "::", b[max(0, mm.start() - 300): mm.start() + 600])
