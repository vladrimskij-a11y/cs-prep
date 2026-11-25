import json, urllib.request, gzip
B = "https://api.bo3.gg/api/v1"
def get(p):
    r = urllib.request.urlopen(urllib.request.Request(B + p, headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0"}), timeout=30)
    b = r.read(); return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
ms = get("/matches?filter[matches.status][eq]=finished&filter[matches.tier][eq]=s&sort=-start_date&page[limit]=3")
m = (ms.get("results") or ms.get("data") or [])[0]
print("match", m.get("id"), m.get("slug"))
gs = get(f"/games?filter[games.match_id][eq]={m['id']}")
g0 = (gs.get("results") or gs.get("data") or gs)[0]
g = get(f"/games/{g0['id']}")
rs = g.get("game_rounds") or []
print("round keys:", sorted(rs[0].keys()) if rs else None)
for r in rs[:4]: print(json.dumps(r, ensure_ascii=False)[:600])
