"""Проверка: как записано вето на страницах турниров Liquipedia (2–3 запроса с паузами)."""
import gzip, json, re, time, urllib.parse, urllib.request, urllib.error
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
out = []
def lp(params):
    url = "https://liquipedia.net/counterstrike/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, json.loads(b)
    except urllib.error.HTTPError as e:
        return e.code, None
time.sleep(20)
st, j = lp({"action": "query", "list": "search", "srsearch": "PGL Masters Bucharest 2026", "srlimit": 5, "srnamespace": 0})
out.append(f"search: {st} {[(x['title']) for x in (j or {}).get('query', {}).get('search', [])]}")
titles = [x["title"] for x in (j or {}).get("query", {}).get("search", [])]
base = next((t for t in titles if "/" in t and "Bucharest" in t), titles[0] if titles else None)
if base:
    time.sleep(10)
    st, j = lp({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": base, "redirects": 1})
    try:
        txt = list(j["query"]["pages"].values())[0]["revisions"][0]["slots"]["main"]["*"]
        out.append(f"## {base}: len={len(txt)} veto={len(re.findall('veto', txt, re.I))} Match={txt.count('{{Match')}")
        out.append("subpages: " + str(sorted(set(re.findall(r"\{\{:?(" + re.escape(base) + r"/[^}|]+)", txt)))[:10]))
        i = txt.lower().find("veto")
        out.append("VETO: " + (txt[max(0, i - 1500):i + 1500] if i >= 0 else txt[:2000]))
    except Exception as e:
        out.append(f"page: {st} {e!r}")
open("probe/out.txt", "w").write("\n".join(out))
