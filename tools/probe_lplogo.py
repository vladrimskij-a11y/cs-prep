import json, time, urllib.parse, urllib.request, re
API = "https://liquipedia.net/counterstrike/api.php"
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}), timeout=30)
    b = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        import gzip; b = gzip.decompress(b)
    return b, r.headers.get("Content-Type")
titles = ["Heroic", "FaZe Clan", "Team Vitality", "Natus Vincere", "MOUZ"]
d = json.loads(get(API + "?" + urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(titles), "redirects": 1, "format": "json"}))[0])
files = []
for pg in d["query"]["pages"].values():
    t = pg["revisions"][0]["slots"]["main"]["*"]
    im = re.search(r"\|\s*image\s*=\s*([^|\n]*)", t); imd = re.search(r"\|\s*imagedark\s*=\s*([^|\n]*)", t)
    print(pg["title"], "| image:", im and im.group(1).strip(), "| dark:", imd and imd.group(1).strip())
    f = (imd and imd.group(1).strip()) or (im and im.group(1).strip())
    if f: files.append(f)
time.sleep(4)
d = json.loads(get(API + "?" + urllib.parse.urlencode({"action": "query", "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 360, "titles": "|".join("File:" + f for f in files), "format": "json"}))[0])
print(json.dumps(d["query"].get("normalized"), ensure_ascii=False)[:400])
for pg in d["query"]["pages"].values():
    ii = (pg.get("imageinfo") or [{}])[0]
    print(pg["title"], ii.get("mime"), ii.get("width"), ii.get("height"), ii.get("url"), ii.get("thumburl"))
    time.sleep(2)
    u = ii.get("thumburl") or ii.get("url")
    if u:
        b, ct = get(u); print("  downloaded", len(b), ct)
