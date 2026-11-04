import json, urllib.request, urllib.parse
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
O = "https://vladrimskij-a11y.github.io"
def req(u, method="GET", extra=None):
    h = {"User-Agent": UA, "Origin": O, "Accept": "application/json"}; h.update(extra or {})
    r = urllib.request.Request(u, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, dict(x.headers), x.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
B = "https://api.bo3.gg/api/v1"
u = B + "/matches?" + urllib.parse.urlencode({"page[limit]": 5, "filter[matches.status][in]": "current,upcoming", "sort": "start_date"})
st, h, b = req(u)
print("PR GET", st, {k: v for k, v in h.items() if k.lower().startswith("access-control") or k.lower() in ("vary", "cache-control")})
st2, h2, _ = req(u, "OPTIONS", {"Access-Control-Request-Method": "GET"})
print("PR OPTIONS", st2, {k: v for k, v in h2.items() if k.lower().startswith("access-control")})
res = json.loads(b).get("results") or []
for m in res[:5]:
    print("PR M", m.get("id"), m.get("slug"), m.get("status"), m.get("team1_score"), m.get("team2_score"), sorted(m.keys())[:80])
cur = next((m for m in res if m.get("status") == "current"), None)
if cur:
    st, h, b = req(B + "/games?" + urllib.parse.urlencode({"filter[games.match_id][eq]": cur["id"]}))
    g = json.loads(b).get("results") or []
    print("PR G", st, [(x.get("id"), x.get("map_name"), x.get("status"), x.get("winner_clan_score"), x.get("loser_clan_score")) for x in g])
    if g:
        print("PR GK", sorted(g[-1].keys()))
