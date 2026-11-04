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
G = "https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings/contents/live/2026"
L = json.loads(get(G))
names = sorted(x["name"] for x in L if x["type"] == "file")
print("NEWEST", names[-8:])
glob = [n for n in names if "global" in n][-1]
md = get("https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/2026/" + glob)
show("global md", md, 2500)
eu = [n for n in names if "europe" in n][-1]
show("europe md", get("https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/2026/" + eu), 1200)
D = json.loads(get(G + "/details"))
print("DETAILS", [x["name"] for x in D][-5:], len(D))
if D:
    last = sorted(x["name"] for x in D)[-1]
    sub = json.loads(get(G + "/details/" + last)) if not last.endswith(".md") else None
    if isinstance(sub, list):
        print("SUB", [x["name"] for x in sub][:10], len(sub))
        f = [x for x in sub if x["type"] == "file"][0]
        show("detail file", get(f["download_url"]), 5000)
    else:
        show("detail", get(D[-1]["download_url"] or ""), 5000)
h = get("https://www.hltv.org/valve-ranking/teams")
i = h.find("ranked-team")
show("hltv vrs block", re.sub(r"\s+", " ", h[i-200:i+3500]), 3700)
t = get("https://www.hltv.org/team/9565/vitality")
print("TEAM LEN", len(t), t[:80])
j = t.find("achievement")
show("hltv team achievements", re.sub(r"\s+", " ", t[j:j+3000]), 3000)
B = "https://api.bo3.gg/api/v1"
show("bo3 tournament stage rounds names", re.sub(r'"bet_updates":\{.*?\]\},', '', get(B + "/tournaments/intel-extreme-masters-beijing-2026"))[4000:9000], 5000)
