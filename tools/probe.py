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
t = get("https://www.hltv.org/team/9565/vitality")
for m in re.finditer(r'achievement', t):
    pass
idx = [m.start() for m in re.finditer(r'achievement-table|achievementsBox|achievement', t)]
print(len(idx), idx[:20])
i = t.find('class="achievement-table')
if i < 0: i = t.find('id="achievementsBox"', t.find('id="achievementsBox"') + 10)
print(re.sub(r"<img[^>]*>", "", re.sub(r"\s+", " ", t[i-200:i+5000])))
