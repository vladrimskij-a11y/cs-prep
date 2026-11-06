import re, sys, time, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(p):
    time.sleep(1.2)
    r = urllib.request.Request("https://www.dust2.us" + p, headers={"User-Agent": UA, "Accept": "text/html"})
    return re.sub(r"\s+", " ", urllib.request.urlopen(r, timeout=40).read().decode("utf-8", "replace"))
b = get("/matches/2400006/parivision-vs-natus-vincere")
cls = re.findall(r'class="([^"]+)"', b)
from collections import Counter
print(Counter(cls).most_common(150))
for k in ("zweih", "ADR", "K-D", "Rating", "stats"):
    for m in list(re.finditer(k, b))[:4]:
        print("##", k, re.sub(r"<[^>]+>", "|", b[m.start()-400:m.start()+400]))
print(re.findall(r'href="(/[^"]*stats[^"]*)"', b)[:20])
print(re.findall(r'href="(/matches/[^"]*)"', b)[:20])
m = re.search(r'map-container-map-name">Mirage<', b); print(b[m.start()-1500:m.start()+3000] if m else "")
