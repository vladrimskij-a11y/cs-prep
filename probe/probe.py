"""Проверка: призовые/слоты и участники мейджора на Liquipedia. Медленно, чтобы не упереться в лимит."""
import gzip, json, re, time, urllib.parse, urllib.request, urllib.error
UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
out = []
def lp(params):
    time.sleep(6)
    url = "https://liquipedia.net/counterstrike/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, json.loads(b)
    except urllib.error.HTTPError as e:
        return e.code, None
time.sleep(60)
for q in ["IEM Beijing 2026", "PGL Major Singapore 2026"]:
    st, j = lp({"action": "opensearch", "search": q, "limit": 5})
    out.append(f"## search {q}: {st} {j}")
    if not j: continue
    title = next((t for t in j[1] if "/" in t or "Major" in t), j[1][0] if j[1] else None)
    if not title: continue
    st, j = lp({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": title, "redirects": 1})
    try:
        txt = list(j["query"]["pages"].values())[0]["revisions"][0]["slots"]["main"]["*"]
    except Exception as e:
        out.append(f"{title}: {st} {e!r}"); continue
    out.append(f"## PAGE {title} len={len(txt)}")
    for key in ["Prize pool", "prize", "qualif", "Participants", "TeamCard", "invite", "Stage 1", "Stage 3"]:
        i = txt.find(key)
        out.append(f"--- [{key}] at {i}\n" + (txt[max(0, i-300):i+2200] if i >= 0 else ""))
open("probe/out.txt", "w").write("\n".join(out))
