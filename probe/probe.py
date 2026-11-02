"""Проверка №5: HLTV — VRS Invites и стадии мейджора."""
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
def clean(h): return re.sub(r"\s+", " ", re.sub(r'<img[^>]*>|<svg.*?</svg>', '', h, flags=re.S))
st, h = get("https://www.hltv.org/valve-ranking/invites")
out.append(f"=== invites: {st} len={len(h)}")
i = h.find('class="contentCol"')
body = clean(h[i:])
for k in ["Stage", "stage", "invite", "Invite", "Next", "next", "Region", "Europe"]:
    j = body.find(k)
    out.append(f"--- [{k}] at {j}\n" + (body[max(0, j-300):j+3000] if j >= 0 else ""))
st, ev = get("https://www.hltv.org/events")
for slug in ["pgl-major-singapore-2026-stage-1", "pgl-major-singapore-2026-stage-2", "pgl-major-singapore-2026"]:
    m = re.search(r'href="(/events/\d+/' + re.escape(slug) + r')"', ev)
    if not m: out.append(f"no {slug}"); continue
    time.sleep(3)
    st, h = get("https://www.hltv.org" + m.group(1))
    b = clean(h)
    teams = re.findall(r'<div class="team-name"><a href="/team/\d+/[^"]+"><div class="text-container"><div class="text">([^<]+)</div>.*?<div class="sub-text event-text">(.*?)</div>', b)
    out.append(f"=== {slug}: {st} teams={len(teams)} {teams[:40]}")
    j = b.find("PrizeDistribution")
    out.append("prize: " + (b[j:j+2500] if j >= 0 else ""))
    j = b.find("qualif")
    out.append("qualif ctx: " + (b[max(0,j-500):j+800] if j >= 0 else ""))
open("probe/out.txt", "w").write("\n".join(out))
