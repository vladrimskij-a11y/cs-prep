import json, urllib.request, urllib.parse
UA = "Mozilla/5.0"
B = "https://api.bo3.gg/api/v1"
def get(p, q=None):
    u = B + p + ("?" + urllib.parse.urlencode(q) if q else "")
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=30) as r:
        return json.loads(r.read())
ms = get("/matches", {"page[limit]": 20, "filter[matches.status][in]": "current", "sort": "-tier_rank"}).get("results") or []
print("PR n", len(ms))
for m in ms[:20]:
    gs = get("/games", {"filter[games.match_id][eq]": m["id"]}).get("results") or []
    cur = next((g for g in gs if g.get("status") == "current"), None)
    if not cur: continue
    print("PR M", m["slug"], m["team1_score"], m["team2_score"], m.get("team1_last_game_score"), m.get("team2_last_game_score"), m.get("live_coverage"), m.get("live_updates"))
    g = get(f"/games/{cur['id']}")
    print("PR GKEYS", sorted(g.keys()))
    print("PR ROUNDS", len(g.get("game_rounds") or []), (g.get("game_rounds") or [])[-1:] )
    print("PR CLANS", {k: g.get(k) for k in ("winner_team_clan", "loser_team_clan", "state", "partially_state")} )
    ps = get(f"/games/{cur['id']}/players_stats")
    print("PR PS", len(ps) if isinstance(ps, list) else type(ps), json.dumps(ps[0] if isinstance(ps, list) and ps else ps)[:600])
    break
