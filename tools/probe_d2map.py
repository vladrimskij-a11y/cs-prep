import json, urllib.request, urllib.parse, time
B = "https://api.bo3.gg/api/v1"
def get(p, q=None):
    time.sleep(0.4)
    u = B + p + ("?" + urllib.parse.urlencode(q) if q else "")
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=30).read())
    except Exception as e:
        return {"err": repr(e)}
res = get("/matches", {"page[limit]": 15, "sort": "start_date", "filter[matches.status][in]": "upcoming", "filter[matches.tier][in]": "s,a"}).get("results") or []
types = {}
for m in res:
    bu = m.get("bet_updates") or {}
    print(m.get("slug"), "provider", bu.get("provider"), "keys", list(bu.keys()))
    for mk in bu.get("additional_markets") or []:
        types[mk.get("bet_type")] = mk
print("BET TYPES", sorted(types))
for k, v in list(types.items())[:40]:
    print(json.dumps(v)[:300])
if res:
    m = res[0]
    print("MATCH KEYS", list(m.keys()))
    for p in (f"/matches/{m['slug']}", f"/matches/{m['id']}/bet_updates", f"/bet_updates?filter[bet_updates.match_id][eq]={m['id']}", f"/games?filter[games.match_id][eq]={m['id']}"):
        r = get(p)
        s = json.dumps(r)
        print("==", p, len(s), s[:200])
        i = s.find("bet"); print("   bet@", s[i-100:i+600] if i >= 0 else "-")
cur = get("/matches", {"page[limit]": 5, "filter[matches.status][in]": "current"}).get("results") or []
for m in cur:
    bu = m.get("bet_updates") or {}
    print("LIVE", m.get("slug"), sorted({x.get("bet_type") for x in bu.get("additional_markets") or []}))
