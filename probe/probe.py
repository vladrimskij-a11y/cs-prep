"""Разовая проверка: роли игроков и тренеры (Liquipedia, bo3.gg)."""
import gzip, json, re, time, urllib.parse, urllib.request, urllib.error
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
out = []
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500].decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)
LP = "https://liquipedia.net/counterstrike/api.php?"
for name in ["Natus Vincere", "The MongolZ", "FURIA"]:
    st, b = get(LP + urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": name, "redirects": 1, "format": "json"}))
    out.append(f"=== LP {name}: {st} len={len(b)}")
    try:
        pages = json.loads(b)["query"]["pages"]
        txt = list(pages.values())[0]["revisions"][0]["slots"]["main"]["*"]
        i = txt.find("Squad")
        out.append(txt[i-200:i+3500])
    except Exception as e:
        out.append(b[:500] + repr(e))
    time.sleep(3)
B = "https://api.bo3.gg/api/v1"
for nm, url in [("bo3 players by team", B + "/players?filter[players.team_id][eq]=787"),
                ("bo3 team with players", B + "/teams?filter[teams.slug][eq]=natus-vincere&with=players")]:
    st, b = get(url)
    out.append(f"=== {nm}: {st} len={len(b)}\n{b[:2500]}")
open("probe/out.txt", "w").write("\n".join(out))
