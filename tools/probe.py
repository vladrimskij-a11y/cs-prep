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
r = get(LP + "?" + urllib.parse.urlencode({"action": "parse", "page": "Intel_Extreme_Masters/2026/Beijing", "prop": "wikitext", "format": "json"}))
print(r[:200]); wt = json.loads(r)["parse"]["wikitext"]["*"]
i = wt.find("{{Match")
print("LP MATCH BLOCK:\n", wt[i:i+2500])
print("veto words:", sorted(set(re.findall(r"\|\s*(\w*veto\w*|\w*ban\w*|\w*pick\w*)\s*=", wt)))[:30])
