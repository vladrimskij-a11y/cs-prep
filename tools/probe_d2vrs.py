import urllib.request, gzip, re, time
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept-Encoding": "gzip"}
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30); b = r.read()
    return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
for mid, slug in (("2400578", "wildcard-vs-chinggis-warriors"), ("2400577", "imperial-vs-liquid")):
    h = get(f"https://www.dust2.us/matches/{mid}/{slug}")
    print(mid, "длина", len(h))
    for w in ("VRS", "vrs", "forecast", "Forecast", "Team wins", "TEAM WINS", "pt<", "Valve"):
        i = h.find(w); print(f"  {w!r}: {h.count(w)}", re.sub(r"\s+", " ", h[max(0, i-150):i+150]) if i >= 0 else "")
    time.sleep(2)
