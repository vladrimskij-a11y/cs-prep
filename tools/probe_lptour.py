import json, time, urllib.parse, urllib.request, gzip
API = "https://liquipedia.net/counterstrike/api.php"
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}), timeout=30)
    b = r.read()
    return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
for q in ["PGL Major Singapore 2026", "PGL Major", "PGL/2026", "PGL Singapore", "BLAST Bounty 2027", "IEM Krakow 2027", "IEM Kraków 2027", "PGL Cluj-Napoca 2027"]:
    d = json.loads(get(API + "?" + urllib.parse.urlencode({"action": "opensearch", "search": q, "limit": 8, "format": "json"})))
    print(q, "->", d[1]); time.sleep(3)
