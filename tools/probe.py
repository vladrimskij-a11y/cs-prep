import re, urllib.request, gzip, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}), timeout=40) as r:
            b = r.read(); return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
    except Exception as e: return "ERR " + repr(e)
h = get("https://www.hltv.org/player/7998/s1mple")
print("PLAYER", len(h), h[:40])
b = re.sub(r"\s+", " ", h)
for k in ("MVP", "majorAchievement", "Major", "trophy"):
    ms = list(re.finditer(k, b))
    print("==", k, len(ms))
    for m in ms[:3]:
        print("   ", re.sub(r"<svg.*?</svg>", "", b[max(0, m.start()-250):m.start()+350]))
i = b.find('id="majorAchievement"'); print("MAJOR TABLE:", b[i:i+1500] if i >= 0 else "none")
time.sleep(3)
r = get("https://www.hltv.org/valve-ranking/teams")
i = r.find('class="ranked-team standard-box"'); print("RANK LOGO:", re.sub(r"\s+", " ", r[i:i+700]))
