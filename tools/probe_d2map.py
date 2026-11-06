import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
K = os.environ.get("ODDSPAPI_KEY", "")
print("key set:", bool(K), "len", len(K))
B = "https://api.oddspapi.io/v4"
def get(p, **q):
    time.sleep(2.2)
    q["apiKey"] = K
    u = B + p + "?" + urllib.parse.urlencode(q)
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"Accept": "application/json", "User-Agent": "rimcs/1.0"}), timeout=40)
        h = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "remain" in k.lower() or "quota" in k.lower()}
        print("  ", p, "headers", h)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("  ", p, "HTTP", e.code, e.read()[:300])
        return None
now = datetime.now(timezone.utc)
fx = get("/fixtures", sportId=17, **{"from": now.strftime("%Y-%m-%d"), "to": (now + timedelta(days=6)).strftime("%Y-%m-%d")})
fl = fx if isinstance(fx, list) else (fx or {}).get("data") or []
print("fixtures", len(fl))
for f in fl[:60]:
    print("  ", json.dumps({k: f.get(k) for k in ("fixtureId", "participant1Name", "participant2Name", "tournamentName", "startTime", "statusId", "hasOdds")}, ensure_ascii=False))
if fl: print("FX KEYS", list(fl[0].keys()))
mk = get("/markets")
ml = mk if isinstance(mk, list) else (mk or {}).get("data") or []
cs = [m for m in ml if m.get("sportId") == 17]
print("CS2 markets", len(cs))
for m in cs:
    print("   ", m.get("marketId"), m.get("marketName"), m.get("handicap"), m.get("period"), [o.get("outcomeName") for o in m.get("outcomes") or []])
want = [f for f in fl if any(x in json.dumps(f).lower() for x in ("mongol", "natus", "mouz", "vitality", "furia"))][:3]
for f in want:
    od = get("/odds", fixtureId=f["fixtureId"])
    bo = (od or {}).get("bookmakerOdds") or {}
    print("ODDS", f.get("participant1Name"), f.get("participant2Name"), "books", len(bo))
    for slug in sorted(bo):
        mm = bo[slug].get("markets") or {}
        print("    ", slug, sorted(mm.keys())[:40])
    pin = bo.get("pinnacle") or {}
    print("  PINNACLE", json.dumps(pin)[:2500])
