import json, os, re, urllib.request, urllib.parse, gzip
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(url, h=None):
    hh = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "text/html,application/json", "Accept-Language": "en-US,en;q=0.9"}; hh.update(h or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hh), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
def show(title, s, n=1500):
    print("=====", title); print(s[:n]); print()
def ctx(h, pat, w=1500, k=3):
    out = []
    for m in list(re.finditer(pat, h))[:k]:
        out.append(re.sub(r"\s+", " ", h[max(0, m.start()-300):m.start()+w]))
    return "\n---\n".join(out)
t = get("https://www.hltv.org/team/9565/vitality")
i = t.find('id="achievementsBox"')
show("achievements box", re.sub(r"<img[^>]*>", "", re.sub(r"\s+", " ", t[i:i+6000])), 6000)
links = re.findall(r'href="(/matches/\d+/[^"]+)"', t)
print("MATCH LINKS", links[:5])
time = __import__("time")
for L in links[:1]:
    time.sleep(3)
    h = get("https://www.hltv.org" + L)
    print("MATCH", L, len(h), h[:60])
    show("vrs ctx", ctx(h, r"(?i)vrs|valve ranking|points", 1200, 5), 8000)
time.sleep(3)
h = get("https://www.hltv.org/results?event=8000")
print("RESULTS", len(h), h[:60])
time.sleep(3)
h = get("https://www.hltv.org/matches")
print("MATCHES", len(h), h[:60])
