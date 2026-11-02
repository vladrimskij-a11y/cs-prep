"""Проверка №2: вето и сетка на Liquipedia, вето на bo3.gg."""
import gzip, json, re, time, urllib.parse, urllib.request, urllib.error
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
out = []
def get(url, accept="application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        try: b = gzip.decompress(b)
        except Exception: pass
        return e.code, b[:300].decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)
LP = "https://liquipedia.net/counterstrike/api.php?"
def lp(params):
    time.sleep(3)
    return get(LP + urllib.parse.urlencode({**params, "format": "json"}))
st, b = lp({"action": "opensearch", "search": "PGL Masters Bucharest 2026", "limit": 5})
out.append(f"search: {st} {b[:300]}")
titles = []
try: titles = json.loads(b)[1]
except Exception: pass
for t in titles[:1] + ["Intel Extreme Masters/2026/Beijing"]:
    st, b = lp({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": t, "redirects": 1})
    try:
        txt = list(json.loads(b)["query"]["pages"].values())[0]["revisions"][0]["slots"]["main"]["*"]
    except Exception as e:
        out.append(f"{t}: {st} {b[:200]} {e!r}"); continue
    out.append(f"## {t}: len={len(txt)} veto={len(re.findall('veto', txt, re.I))} Match2={txt.count('{{Match')} bracket={txt.count('Bracket|')}")
    i = txt.lower().find("veto")
    if i >= 0: out.append("VETO CTX: " + txt[max(0,i-1500):i+800])
    j = txt.find("{{Match")
    if j >= 0: out.append("MATCH CTX: " + txt[j:j+1500])
    subs = re.findall(r"\{\{:?([^{}|]*?/(?:Playoffs|Group Stage|Swiss Stage|Stage \d)[^{}|]*)\}\}", txt)
    out.append(f"transcluded: {subs[:6]}")
    for sp in subs[:1]:
        st, b = lp({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": sp.strip(), "redirects": 1})
        try:
            tx = list(json.loads(b)["query"]["pages"].values())[0]["revisions"][0]["slots"]["main"]["*"]
            k = tx.lower().find("veto")
            out.append(f"### sub {sp}: len={len(tx)} veto={len(re.findall('veto', tx, re.I))}\n" + (tx[max(0,k-1200):k+600] if k>=0 else tx[:1500]))
        except Exception as e:
            out.append(f"sub {sp}: {e!r}")
B = "https://api.bo3.gg/api/v1"
for nm, url in [("bo3 show-match games,teams", f"{B}/matches/vitality-vs-natus-vincere-29-10-2026?scope=show-match&with=games,teams"),
                ("bo3 show-match", f"{B}/matches/vitality-vs-natus-vincere-29-10-2026?scope=show-match"),
                ("bo3 by id", f"{B}/matches/132248")]:
    st, b = get(url)
    try:
        j = json.loads(b); out.append(f"=== {nm}: {st} keys={list(j.keys())}")
        for k in j:
            if re.search("pick|veto|ban|map", k, re.I): out.append(f"   {k} = {json.dumps(j[k])[:800]}")
    except Exception:
        out.append(f"=== {nm}: {st} {b[:200]}")
open("probe/out.txt", "w").write("\n".join(out))
