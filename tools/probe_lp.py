import re, sys
sys.path.insert(0, "collector")
import collect as C
P = C.lp_pages(["FalleN", "Zweih", "Sidde"])
for k, v in P.items():
    print(k, len(v), [v[max(0, m.start()-80):m.start()+80].replace("\n", " ") for m in re.finditer(r"(?i)hltv", v)][:5])
