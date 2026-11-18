import sys, re
sys.path.insert(0, "collector")
import collect as c
h = c.hltv_get("/events") or ""
print("LEN", len(h))
for m in re.finditer(r'href="/events/\d+/blast-bounty[^"]*"', h):
    s = h[max(0, m.start() - 300): m.start() + 2500]
    print("=====", re.sub(r"\s+", " ", s)); break
i = h.find('href="/events/'); print("FIRST", re.sub(r"\s+", " ", h[i-300:i+1800]))
