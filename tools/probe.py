import json, os, sys, urllib.request, urllib.parse, gzip
UA = "Mozilla/5.0 (compatible; cs-prep-bot/1.0)"
def get(url, h=None):
    hh = {"User-Agent": UA, "Accept-Encoding": "gzip"}; hh.update(h or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hh), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
def show(title, s, n=1500):
    print("=====", title); print(s[:n]); print()
B = "https://api.bo3.gg/api/v1"
T = os.environ.get("PANDASCORE_TOKEN", "")
show("bo3 tournaments current", get(B + "/tournaments?" + urllib.parse.urlencode({"page[limit]": 5, "filter[tournaments.status][in]": "current", "filter[tournaments.tier][in]": "s"})), 3000)
j = get(B + "/tournaments?" + urllib.parse.urlencode({"page[limit]": 5, "filter[tournaments.status][in]": "current", "filter[tournaments.tier][in]": "s"}))
try:
    tid = json.loads(j)["results"][0]["id"]; slug = json.loads(j)["results"][0]["slug"]
except Exception: tid = None; slug = None
print("tid", tid, slug)
if tid:
    show("bo3 matches by tournament", get(B + "/matches?" + urllib.parse.urlencode({"page[limit]": 3, "filter[matches.tournament_id][eq]": tid, "filter[matches.status][in]": "finished", "with": "teams,tournament,games"})), 4000)
    show("bo3 tournament detail", get(B + f"/tournaments/{slug}"), 4000)
    show("bo3 tournament prize", get(B + f"/tournaments/{tid}/prize_distributions"), 1500)
    show("bo3 tournament standings", get(B + "/tournament_teams?" + urllib.parse.urlencode({"filter[tournament_teams.tournament_id][eq]": tid, "page[limit]": 3})), 2000)
show("bo3 team vitality", get(B + "/teams?" + urllib.parse.urlencode({"filter[teams.slug][eq]": "vitality"})), 2500)
show("bo3 team achievements", get(B + "/teams/vitality/achievements"), 1500)
show("bo3 team tournaments", get(B + "/tournament_teams?" + urllib.parse.urlencode({"filter[tournament_teams.team_id][eq]": 1, "page[limit]": 3, "sort": "-id"})), 2000)
show("valve repo", get("https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings/contents/live/2026"), 3000)
show("panda past", get("https://api.pandascore.co/csgo/matches/past?" + urllib.parse.urlencode({"per_page": 2, "filter[serie_id]": 11136}), {"Authorization": "Bearer " + T}), 3000)
show("hltv ranking", get("https://www.hltv.org/valve-ranking/teams", {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}), 300)
