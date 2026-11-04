import re, time, urllib.request, gzip
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}), timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
h = get("https://www.dust2.us/matches/2399855/legacy-vs-the-mongolz")
print("MATCH", len(h), h[:80])
b = re.sub(r"\s+", " ", h); i = b.find("maps-container")
print("MAPS", b[i:i+2500] if i >= 0 else "no maps-container")
for k in ("veto", "banned", "BANNED", "picked", "removed"):
    j = b.find(k); print(k, b[max(0, j-200):j+300] if j >= 0 else "-")
time.sleep(3)
r = get("https://www.dust2.us/results?team=9565")
print("RESULTS", len(r), r[:80]); print(re.findall(r'href="([^"]*matches/\d+/[^"]+)"', r)[:8])
