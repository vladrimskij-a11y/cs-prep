import re, sys, time, urllib.request, gzip
sys.path.insert(0, "collector")
import collect as C
def get(u):
    time.sleep(3)
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": C.HLTV_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}), timeout=40)
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
        return re.sub(r"\s+", " ", b.decode("utf-8", "replace"))
    except Exception as e:
        print("ERR", u, repr(e)); return ""
for u in ("https://www.hltv.org/valve-ranking/teams", "https://www.dust2.us/matches/2400008/the-mongolz-vs-natus-vincere"):
    b = get(u)
    cs = sorted(set(re.findall(r'href="(/coach/\d+/[^"]+)"', b)))
    print(u, len(b), "coach links", len(cs), cs[:15], "word coach", b.lower().count("coach"))
    i = b.lower().find("coach"); print("  ctx", re.sub(r"<[^>]+>", "|", b[i-300:i+300]) if i >= 0 else "")
b = get("https://www.hltv.org/events/8302/pgl-major-singapore-2026")
cs = sorted(set(re.findall(r'href="(/coach/\d+/[^"]+)"', b)))
print("event", len(b), cs[:20], b.lower().count("coach"))
ev = sorted(set(re.findall(r'href="(/events/\d+/[^"]+)"', b)))
target = cs[0] if cs else "/coach/9/zonic"
c = get("https://www.hltv.org" + target)
print("COACH", target, len(c))
for k in ("Majors", "Major", "achievement-table", "trophy", "coachAchievement", "majorAchievement", "Total", "Coaching"):
    print(k, c.count(k))
for m in list(re.finditer(r'<div class="stat">', c))[:10]:
    print("  stat", re.sub(r"<[^>]+>", "|", c[m.start():m.start()+200]))
i = c.find("achievement-table"); print("ACH", re.sub(r"<img[^>]*>", "", c[i-500:i+3000]) if i >= 0 else "")
i = c.find("trophy"); print("TRO", re.sub(r"<img[^>]*title=\"([^\"]+)\"[^>]*>", r"[\1]", c[i-300:i+2000]) if i >= 0 else "")
