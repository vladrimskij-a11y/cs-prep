import re, sys
sys.path.insert(0, "collector")
import collect as C
P = C.lp_pages(["FalleN", "Zweih", "B1ad3", "Sidde", "Molodoy", "HObbit"])
for k, v in P.items():
    ib = C.infobox(v)
    print(k, [l.strip() for l in ib.split("\n") if re.search(r"(?i)hltv|\|\s*id\s*=", l)][:6])
