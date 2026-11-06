import re, sys, time, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(p):
    time.sleep(1.2)
    r = urllib.request.Request("https://www.dust2.us" + p, headers={"User-Agent": UA, "Accept": "text/html"})
    return re.sub(r"\s+", " ", urllib.request.urlopen(r, timeout=40).read().decode("utf-8", "replace"))
def show(path):
    b = get(path)
    i = b.find('class="maps-container"')
    sec = b[i:i + 30000] if i >= 0 else ""
    print("==", path, "len", len(b), "maps-container", i)
    for n, box in enumerate(re.split(r'<div class="standard-box map-container ', sec)[1:], 1):
        t = re.sub(r"<[^>]+>", "|", box[:3000]); t = re.sub(r"(\|\s*)+", "|", t)
        print(n, t[:500])
    for k in ("results-team-score", "mapholder", "score", "won", "lost", "live"):
        print(k, b.count(k))
    j = b.find("results-team-score"); print(b[j-300:j+600] if j >= 0 else "")
lst = get("/results")
links = list(dict.fromkeys(re.findall(r'href="(/matches/\d+/[^"#?]+)"', lst)))
want = sys.argv[1:] or ["parivision-vs-natus-vincere"]
for w in want:
    for l in links:
        if w in l:
            show(l); break
up = get("/matches")
live = list(dict.fromkeys(re.findall(r'href="(/matches/\d+/[^"#?]+)"', up)))
for l in live:
    if "furia" in l and "betboom" in l:
        show(l); break
