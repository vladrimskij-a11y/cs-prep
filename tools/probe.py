import re, urllib.request, gzip, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "text/html"}), timeout=40) as r:
        b = r.read(); return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
h = get("https://www.hltv.org/results?team=12467")
print("PR heads", re.findall(r'Results for [^<]+', h)[:5])
ms = re.findall(r'href="/matches/(\d+)/([^"#?]+)"', h)[:6]; print("PR ms", ms)
time.sleep(3)
mid, slug = ms[0]
d = get(f"https://www.dust2.us/matches/{mid}/{slug}")
print("PR unix", re.findall(r'data-unix="(\d+)"', d)[:5], "len", len(d), "maps-container" in d)
i = d.find('class="maps-container"'); print("PR seg", re.sub(r"<img[^>]*>","",re.sub(r"\s+"," ",d[i:i+1500])))
