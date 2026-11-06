import json, os, time, urllib.request, urllib.parse
K = os.environ.get("ODDSPAPI_KEY", "")
B = "https://api.oddspapi.io/v4"
def get(p, **q):
    time.sleep(2.2)
    q["apiKey"] = K
    try:
        r = urllib.request.urlopen(urllib.request.Request(B + p + "?" + urllib.parse.urlencode(q), headers={"Accept": "application/json", "User-Agent": "rimcs/1.0"}), timeout=60)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("HTTP", p, e.code, e.read()[:300]); return None
cat = {m["marketId"]: m for m in (get("/markets") or []) if m.get("sportId") == 17}
onames = {m["marketId"]: {o["outcomeId"]: o["outcomeName"] for o in m.get("outcomes") or []} for m in cat.values()}
for fid, label in (("id1704204776501852", "MGLZ-NAVI"), ("id1704204776437958", "FURIA-BB"), ("id1703162175955628", "MOUZ-G2")):
    od = get("/odds", fixtureId=fid) or {}
    bo = od.get("bookmakerOdds") or {}
    print("=====", label, "books", len(bo), "fixture keys", [k for k in od.keys() if k != "bookmakerOdds"])
    cnt = {}
    for slug, b in bo.items():
        for mid in (b.get("markets") or {}):
            nm = (cat.get(int(mid)) or {}).get("marketName", "?")
            if "Round" in nm or "Map Winner" in nm:
                cnt.setdefault(nm, set()).add(slug)
    for nm, s in sorted(cnt.items()):
        print("  ", nm, len(s), "pinnacle" in s, sorted(s)[:8])
    for slug in ("pinnacle", "polymarket", "betfair-ex", "thunderpick"):
        b = bo.get(slug) or {}
        print("  --", slug, "markets", len(b.get("markets") or {}))
        for mid, mk in sorted((b.get("markets") or {}).items(), key=lambda kv: int(kv[0])):
            m = cat.get(int(mid)) or {}
            nm = m.get("marketName", "?")
            if not ("Round" in nm or "Map Winner" in nm or nm == "Winner" or "Correct Map" in nm):
                continue
            outs = []
            for oid, o in (mk.get("outcomes") or {}).items():
                pr = ((o.get("players") or {}).get("0") or {}).get("price")
                outs.append(f'{onames.get(int(mid), {}).get(int(oid), oid)}={pr}')
            print("     ", mid, nm, m.get("handicap"), m.get("period"), outs)
# historical availability for a finished tier-1 match
h = get("/historical-odds", fixtureId="id1704204776437956", bookmakers="pinnacle")
s = json.dumps(h)[:1500] if h else None
print("HIST PRV-NAVI", s)
