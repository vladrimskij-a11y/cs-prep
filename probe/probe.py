import gzip, json, re, time, urllib.parse, urllib.request
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
def get(params):
    url = "https://liquipedia.net/counterstrike/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
        return json.loads(b)
out = []
d = get({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": 1, "format": "json",
         "titles": "Latto|Dumau|Brollan|Alex666|Kyousuke|Xelex|Magnojez|Npl|Jame|Try"})
for pg in d["query"]["pages"].values():
    try: t = pg["revisions"][0]["slots"]["main"]["*"]
    except Exception: out.append(f"## {pg.get('title')} MISSING"); continue
    box = t[:t.find("\n}}")+3]
    out.append(f"## {pg['title']}\n" + "\n".join(l for l in box.split("\n") if re.search(r"role|status|team|igl|captain|position", l, re.I)))
time.sleep(3)
for q in ["TheMongolz", "9z", "The Mongolz", "Heroic"]:
    out.append(f"## opensearch {q}: {get({'action':'opensearch','search':q,'limit':5,'format':'json'})}")
    time.sleep(3)
open("probe/out.txt", "w").write("\n".join(out))
