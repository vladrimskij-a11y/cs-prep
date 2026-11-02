"""Проверка: вето на dust2.us (сайт-партнёр HLTV) и альтернативах."""
import gzip, re, urllib.request, urllib.error, time
out = []
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
                                             "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return -1, repr(e)
for u in ["https://www.dust2.us/matches/2367883/betboom-vs-b8", "https://www.dust2.us/results?team=4608",
          "https://www.dust2.dk/matches/2367883/betboom-vs-b8", "https://bo3.gg/matches/heroic-vs-b8-02-11-2026",
          "https://escorenews.com/en/csgo/matches"]:
    st, h = get(u)
    i = h.find("veto")
    lines = re.findall(r"\d\. [^<]{3,40} (?:removed|picked) [A-Za-z0-9]+", h)
    out.append(f"=== {u}: {st} len={len(h)} veto_at={i} lines={lines[:7]}")
    time.sleep(3)
open("probe/out.txt", "w").write("\n".join(out))
