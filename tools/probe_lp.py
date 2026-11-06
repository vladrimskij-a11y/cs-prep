import re, sys, time, urllib.request, gzip
sys.path.insert(0, "collector")
import collect as C
r = urllib.request.urlopen(urllib.request.Request("https://www.hltv.org/valve-ranking/teams", headers={"User-Agent": C.HLTV_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}), timeout=40)
b = r.read()
if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
b = re.sub(r"\s+", " ", b.decode("utf-8", "replace"))
links = re.findall(r'href="/player/(\d+)/([^"/?#]+)"', b)
print("links", len(links), "unique", len(set(links)))
for k in ("device", "chopper", "syph0", "kairon", "jboen", "techno4k", "neityu"):
    m = re.search(r'.{300}href="/player/\d+/' + k + r'[^"]*".{400}', b)
    print("==", k, re.sub(r'src(set)?="[^"]*"', "", m.group(0)) if m else None)
teams = re.findall(r'class="ranked-team standard-box"', b); print("teams", len(teams))
