"""Проверка: есть ли пики/баны на bo3.gg (match_maps и др.)."""
import gzip, json, urllib.parse, urllib.request, urllib.error
out = []
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (compatible; cs-prep-bot/1.0)", "Accept-Encoding": "gzip", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
B = "https://api.bo3.gg/api/v1"
st, b = get(B + "/matches?" + urllib.parse.urlencode({"page[limit]": 8, "sort": "-start_date", "filter[matches.status][in]": "finished", "filter[matches.tier][eq]": "s"}))
ms = json.loads(b).get("results", []) if st == 200 else []
for m in ms:
    for nm, url in [("show", f"{B}/matches/{m['slug']}?scope=show-match&with=games,match_maps"),
                    ("match_maps filter", f"{B}/match_maps?filter[match_maps.match_id][eq]={m['id']}"),
                    ("nested", f"{B}/matches/{m['id']}/match_maps")]:
        st, b = get(url)
        try:
            j = json.loads(b)
            mm = j.get("match_maps") if isinstance(j, dict) and "match_maps" in j else j.get("results") if isinstance(j, dict) else j
            out.append(f"{m['slug']} {nm}: {st} {json.dumps(mm)[:600]}")
        except Exception:
            out.append(f"{m['slug']} {nm}: {st} {b[:150]}")
open("probe/out.txt", "w").write("\n".join(out))
