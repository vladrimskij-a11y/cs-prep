"""Проверка №3: турниры на bo3.gg — призовые по местам, слоты, участники мейджора."""
import gzip, json, urllib.parse, urllib.request, urllib.error
out = []
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; cs-prep-bot/1.0)", "Accept-Encoding": "gzip", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
B = "https://api.bo3.gg/api/v1"
st, b = get(B + "/tournaments?" + urllib.parse.urlencode({"page[limit]": 8, "sort": "start_date", "filter[tournaments.tier][eq]": "s", "filter[tournaments.status][in]": "upcoming,current"}))
out.append(f"tournaments: {st}")
ts = json.loads(b).get("results", []) if st == 200 else []
for t in ts:
    out.append(f"  {t.get('id')} {t.get('slug')} | {t.get('name')} | {t.get('start_date')} prize={t.get('prize')}")
if ts:
    out.append("keys: " + ", ".join(ts[0].keys()))
want = [t for t in ts if "beijing" in (t.get("slug") or "")][:1] + [t for t in ts if "major" in (t.get("slug") or "") or "singapore" in (t.get("slug") or "")][:1]
for t in want:
    for nm, url in [("show", f"{B}/tournaments/{t['slug']}"),
                    ("show with", f"{B}/tournaments/{t['slug']}?with=prize_distributions,tournament_teams,stages,teams"),
                    ("prize_distributions", f"{B}/prize_distributions?filter[prize_distributions.tournament_id][eq]={t['id']}"),
                    ("tournament_teams", f"{B}/tournament_teams?filter[tournament_teams.tournament_id][eq]={t['id']}&page[limit]=40"),
                    ("stages", f"{B}/stages?filter[stages.tournament_id][eq]={t['id']}")]:
        st, b = get(url)
        out.append(f"=== {t['slug']} {nm}: {st}\n{b[:3000]}")
open("probe/out.txt", "w").write("\n".join(out))
