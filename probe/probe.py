"""Проверка №4: HLTV — призовые по местам, слоты, участники мейджора."""
import gzip, re, urllib.request, urllib.error, time
out = []
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
                                               "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
st, h = get("https://www.hltv.org/events")
out.append(f"events: {st} len={len(h)}")
evs = re.findall(r'href="(/events/(\d+)/([^"]+))"', h)
seen = []
for href, i, slug in evs:
    if slug not in [s for _, s in seen]:
        seen.append((href, slug))
out.append("events: " + " | ".join(s for _, s in seen[:40]))
for key in ["beijing", "major"]:
    m = next((href for href, slug in seen if key in slug and "2026" in slug), None)
    if not m: continue
    time.sleep(3)
    st, h = get("https://www.hltv.org" + m)
    out.append(f"=== {m}: {st} len={len(h)}")
    for k in ["Prize distribution", "prize-distribution", "placement", "qualif", "Teams attending", "team-box", "Invited", "VRS", "Stage"]:
        i = h.find(k)
        out.append(f"--- [{k}] at {i}\n" + (re.sub(r"\s+", " ", h[max(0, i-200):i+2500]) if i >= 0 else ""))
open("probe/out.txt", "w").write("\n".join(out))
