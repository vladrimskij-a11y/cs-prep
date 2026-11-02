"""Проверка №6: HLTV VRS-прогноз участников мейджора (кто в очереди)."""
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
def clean(h): return re.sub(r"\s+", " ", re.sub(r'<img[^>]*>|<svg.*?</svg>|<source[^>]*>', '', h, flags=re.S))
st, h = get("https://www.hltv.org/valve-ranking/invites")
evs = re.findall(r'<a href="(/valve-ranking/teams/event/\d+)" class="ranking-vrs-event">.*?ranking-vrs-event-header">([^<]+)<', h, re.S)
out.append(f"vrs events: {evs}")
target = [e for e in evs if "Major" in e[1] or "Singapore" in e[1]]
for href, name in target[:2]:
    time.sleep(3)
    st, p = get("https://www.hltv.org" + href)
    b = clean(p)
    i = b.find('class="contentCol"')
    out.append(f"=== {name} {href}: {st} len={len(p)}\n" + b[i:i+9000])
open("probe/out.txt", "w").write("\n".join(out))
