import json, sys, os
sys.path.insert(0, "collector")
import collect as c
_, posts = c.tg_fetch_channel("cs3news")
for p in posts:
    if p["id"] in (19554, 19555, 19556, 19557):
        print(p["id"], repr(p["text"][:300]), p["photo"] and p["photo"][:60])
urls = json.load(open("urls.json"))
hs = {}
for k, u in urls.items():
    try:
        hs[k] = c._dhash(c.http_get(u, {"Accept": "image/*"}, timeout=20))
    except Exception as e:
        hs[k] = None; print(k, e)
print(hs)
ks = list(hs)
for i in ks:
    print(i, [bin(int(hs[i], 16) ^ int(hs[j], 16)).count("1") if hs[i] and hs[j] else None for j in ks])
