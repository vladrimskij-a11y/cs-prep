import json, re, time, urllib.request, urllib.parse, gzip
def get(url, h=None):
    hh = {"User-Agent": "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)", "Accept-Encoding": "gzip"}; hh.update(h or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hh), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
time.sleep(5)
LP = "https://liquipedia.net/counterstrike/api.php"
time.sleep(40)
r = get(LP + "?" + urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "Intel_Extreme_Masters/2026/Beijing", "format": "json", "formatversion": 2}))
print(r[:200]); wt = json.loads(r)["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
i = wt.find("{{Match")
print("LP MATCH BLOCK:\n", wt[i:i+2500])
print("veto words:", sorted(set(re.findall(r"\|\s*(\w*veto\w*|\w*ban\w*|\w*pick\w*)\s*=", wt)))[:30])
