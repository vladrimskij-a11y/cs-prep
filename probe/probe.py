"""Разовая проверка №3: поиск команды на bo3.gg, перевод, детали ответа нейросети."""
import gzip, json, os, urllib.parse, urllib.request, urllib.error

UA = "Mozilla/5.0 (compatible; cs-prep-bot/1.0)"
out = []


def get(url, headers=None, data=None, method=None):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, dict(r.headers), body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
        return e.code, dict(e.headers), b.decode("utf-8", "replace")
    except Exception as e:
        return -1, {}, repr(e)


def log(name, st, hd, body, n=700):
    out.append(f"=== {name}: {st} len={len(body)}\n{body[:n]}\n")


tok = os.environ.get("GITHUB_TOKEN", "")
for model in ["openai/gpt-4.1-nano", "openai/gpt-4o"]:
    st, hd, b = get("https://models.github.ai/inference/chat/completions",
                    {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "application/json"},
                    json.dumps({"model": model, "messages": [{"role": "user", "content": "say hi"}]}).encode(), "POST")
    out.append(f"=== models {model}: {st} headers={json.dumps(hd)[:900]}\nbody={b[:300]}\n")
st, hd, b = get("https://models.github.ai/catalog/models", {"Authorization": f"Bearer {tok}", "Accept": "application/json"})
log("models catalog", st, hd, b, 300)

q = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t", "q": "NAVI sign iM to two-year extension"})
st, hd, b = get("https://translate.googleapis.com/translate_a/single?" + q)
log("google translate", st, hd, b, 300)

B = "https://api.bo3.gg/api/v1"
for name, url in [
    ("team by name", B + "/teams?" + urllib.parse.urlencode({"filter[teams.name][eq]": "Natus Vincere"})),
    ("team by slug furia", B + "/teams?filter[teams.slug][eq]=furia"),
    ("team by id", B + "/teams/787"),
    ("team search", B + "/teams?" + urllib.parse.urlencode({"search": "MOUZ", "page[limit]": "3"})),
    ("matches with teams", B + "/matches?page[limit]=1&sort=-start_date&filter[matches.status][in]=finished&filter[matches.team_ids][overlap]=787&with=teams,tournament"),
]:
    st, hd, b = get(url, {"Accept": "application/json"})
    if st == 200 and b.startswith("{"):
        try:
            j = json.loads(b)
            res = j.get("results", j)
            if isinstance(res, list):
                b = json.dumps({"total": j.get("total"), "first": [{k: r.get(k) for k in ("id", "slug", "name", "ps_id", "acronym", "team1", "team2", "tournament") if k in r} for r in res[:3]]}, ensure_ascii=False)
        except Exception:
            pass
    log(name, st, hd, b, 1500)

open("probe/out.txt", "w").write("\n".join(out))
