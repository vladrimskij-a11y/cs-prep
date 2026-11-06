import json, os, re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
K = os.environ.get("ODDSPAPI_KEY", "")
B = "https://api.oddspapi.io/v4"
calls = [0]
def get(p, **q):
    time.sleep(2.3); calls[0] += 1
    q["apiKey"] = K
    try:
        r = urllib.request.urlopen(urllib.request.Request(B + p + "?" + urllib.parse.urlencode(q), headers={"Accept": "application/json", "User-Agent": "rimcs/1.0"}), timeout=90)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("HTTP", p, e.code, e.read()[:300]); return None
cat = [m for m in (get("/markets") or []) if m.get("sportId") == 17]
keep = {m["marketId"]: m for m in cat if re.search(r"Map Winner|Rounds Handicap (First|Second|Third) Map|^Total Rounds (First|Second|Third) Map|^Winner$|Correct Map Score", m.get("marketName") or "")}
TIER1 = re.compile(r"(?i)intel extreme|iem|blast|pgl|esl pro|starladder|thunderpick|fissure|major|esports world cup|cct global|perfect world")
now = datetime.now(timezone.utc)
fx = []
for k in range(7):
    a = now - timedelta(days=9 * (k + 1)); b = now - timedelta(days=9 * k)
    r = get("/fixtures", sportId=17, **{"from": a.strftime("%Y-%m-%d"), "to": b.strftime("%Y-%m-%d")}) or []
    fx += [f for f in r if TIER1.search(f.get("tournamentName") or "") and f.get("hasOdds")]
fx = list({f["fixtureId"]: f for f in fx}.values())
fx = [f for f in fx if (f.get("startTime") or "") < (now - timedelta(hours=6)).isoformat()]
fx.sort(key=lambda f: f["startTime"], reverse=True)
print("tier1 fixtures", len(fx))
out = {"markets": keep, "fixtures": []}
for f in fx[:110]:
    h = get("/historical-odds", fixtureId=f["fixtureId"], bookmakers="pinnacle,polymarket,1xbet")
    if not h:
        continue
    bk = h.get("bookmakers") or {}
    slim = {}
    for slug, b in bk.items():
        mm = {}
        for mid, m in (b.get("markets") or {}).items():
            if int(mid) not in keep:
                continue
            oo = {}
            for oid, o in (m.get("outcomes") or {}).items():
                snaps = (o.get("players") or {}).get("0") or []
                pre = [s for s in snaps if s.get("createdAt", "") < f["startTime"] and s.get("active", True)]
                if pre:
                    oo[oid] = pre[-1]["price"]
            if oo:
                mm[mid] = oo
        slim[slug] = mm
    out["fixtures"].append({k: f.get(k) for k in ("fixtureId", "participant1Name", "participant2Name", "tournamentName", "startTime")} | {"odds": slim})
    print(f["startTime"][:10], f["participant1Name"], "-", f["participant2Name"], {s: len(v) for s, v in slim.items()})
print("calls", calls[0])
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/op_hist.json", "w"))
