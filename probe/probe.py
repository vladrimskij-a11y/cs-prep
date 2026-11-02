"""Проверка №2: призовые/слоты и участники мейджора на Liquipedia (по точным названиям страниц)."""
import gzip, json, re, time, urllib.parse, urllib.request, urllib.error
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
out = []
def lp(params):
    time.sleep(6)
    url = "https://liquipedia.net/counterstrike/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, json.loads(b)
    except urllib.error.HTTPError as e:
        return e.code, None
def pages(titles):
    st, j = lp({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(titles), "redirects": 1})
    res = {}
    if not j: out.append(f"query {titles}: {st}"); return res
    for pg in j["query"]["pages"].values():
        if "revisions" in pg: res[pg["title"]] = pg["revisions"][0]["slots"]["main"]["*"]
        else: out.append(f"missing: {pg.get('title')}")
    return res
time.sleep(30)
P = pages(["Majors", "Intel Extreme Masters/2026/Beijing"])
m = P.get("Majors", "")
links = sorted(set(re.findall(r"\[\[([^\]|]*Singapore[^\]|]*)", m)))
out.append(f"Majors links with Singapore: {links}")
b = P.get("Intel Extreme Masters/2026/Beijing", "")
for key in ["{{Prize pool", "prize pool", "qualifies", "{{Slot", "Prizepool", "points"]:
    i = b.find(key)
    out.append(f"--- IEM [{key}] at {i}\n" + (b[max(0, i-200):i+2500] if i >= 0 else ""))
if links:
    S = pages([links[0]])
    t = S.get(links[0], "")
    out.append(f"## MAJOR {links[0]} len={len(t)}")
    for key in ["Prize pool", "Participants", "Stage 1", "Stage 2", "Stage 3", "TeamCard", "invite", "Invite", "VRS"]:
        i = t.find(key)
        out.append(f"--- [{key}] at {i}\n" + (t[max(0, i-200):i+2500] if i >= 0 else ""))
    subs = sorted(set(re.findall(r"\[\[(" + re.escape(links[0]) + r"/[^\]|]*)", t)))
    out.append(f"subpages: {subs}")
open("probe/out.txt", "w").write("\n".join(out))
