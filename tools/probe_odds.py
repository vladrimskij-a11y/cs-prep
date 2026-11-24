import sys, json, re
sys.path.insert(0, "collector")
import collect as c
c.hltv_get = lambda *a, **k: None
pairs = [(1527, 464, "2400566/eternal-fire-vs-100-thieves"), (10844, 9974, "2400572/bcgame-vs-chinggis-warriors"),
         (8171, 791, "2400567/parivision-vs-faze"), (785, 790, "2400571/ninjas-in-pyjamas-vs-liquid")]
for a, b, d2 in pairs:
    res = c.bo3_team_matches(a, {"page[limit]": 20, "sort": "start_date", "filter[matches.status][in]": "upcoming,current"})
    m = next((x for x in res if {x.get("team1_id"), x.get("team2_id")} == {a, b}), None)
    print("BO3", a, b, "found", bool(m), len(res), m and m.get("start_date"), m and m.get("status"))
    if m:
        print("  bet_updates:", json.dumps(m.get("bet_updates"))[:600])
        print("  keys:", [k for k in m if "bet" in k or "odd" in k or "coef" in k])
        try:
            full = c.bo3(f"/matches/{m.get('slug')}", {})
            print("  full keys with bet/odd:", {k: json.dumps(v)[:300] for k, v in (full or {}).items() if re.search("bet|odd|coef", k)})
        except Exception as e:
            print("  full err", e)
    h = c.d2_get(f"/matches/{d2}") or ""
    print("D2", d2, len(h))
    for kw in ("odds", "Odds", "betting", "coeff", "provider", "data-odds"):
        for mm in list(re.finditer(kw, h))[:3]:
            print("   ", kw, "|", re.sub(r"\s+", " ", h[max(0, mm.start() - 250): mm.start() + 350]))
    open(f"/tmp/d2_{d2.split('/')[0]}.html", "w").write(h)
