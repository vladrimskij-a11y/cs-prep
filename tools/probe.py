import re, urllib.request, gzip, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}), timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
for u in ["https://www.dust2.us/matches/2399852/vitality-vs-falcons", "https://www.dust2.us/matches"]:
    h = get(u); b = re.sub(r"\s+", " ", h)
    print("=====", u, len(h), h[:60])
    for k in ("forecast", "VRS", "Valve"):
        for m in list(re.finditer(k, b))[:2]:
            print(k, "::", re.sub(r"<img[^>]*>", "", b[max(0, m.start()-200):m.start()+1500]))
    print("links", re.findall(r'href="(/matches/\d+/[^"]+)"', h)[:5])
    time.sleep(3)
