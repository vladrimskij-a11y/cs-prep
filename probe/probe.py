"""Разовая проверка: какие источники доступны с серверов GitHub. Результат пишется в probe/out.txt."""
import gzip, json, os, urllib.request, urllib.error

UA = "cs-prep-bot/1.0 (github.com/vladrimskij-a11y/cs-prep)"
out = []


def get(url, headers=None, data=None, method=None):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
        return e.code, b.decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)


def log(name, st, body, n=700):
    out.append(f"=== {name}: {st} len={len(body)}\n{body[:n]}\n")


tok = os.environ.get("GITHUB_TOKEN", "")
for model in ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"]:
    for fmt in (True, False):
        payload = {"model": model, "messages": [{"role": "user", "content": 'Верни JSON {"ok": true}'}]}
        if fmt:
            payload["response_format"] = {"type": "json_object"}
        st, b = get("https://models.github.ai/inference/chat/completions",
                    {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "application/json"},
                    json.dumps(payload).encode(), "POST")
        log(f"models {model} fmt={fmt}", st, b, 400)

st, b = get("https://liquipedia.net/counterstrike/api.php?action=parse&page=Natus_Vincere/Played_Matches&format=json&prop=text")
log("liquipedia played matches", st, b, 300)
if st == 200:
    import re
    txt = json.loads(b).get("parse", {}).get("text", {}).get("*", "")
    out.append("maps mentioned: " + str({m: txt.count(m) for m in ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Train"]}))
    i = txt.find("Mirage")
    out.append("ctx: " + re.sub(r"\s+", " ", txt[max(0, i - 600):i + 200]))

st, b = get("https://liquipedia.net/counterstrike/api.php?action=parse&page=Intel_Extreme_Masters/2026/Beijing&format=json&prop=text")
log("liquipedia tournament", st, b, 200)
if st == 200:
    txt = json.loads(b).get("parse", {}).get("text", {}).get("*", "")
    out.append("tournament maps mentioned: " + str({m: txt.count(m) for m in ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Train"]}))

for name, url in [
    ("bo3 matches", "https://api.bo3.gg/api/v1/matches?page[limit]=2&sort=-start_date&filter[matches.status][in]=finished"),
    ("bo3 teams", "https://api.bo3.gg/api/v1/teams?filter[teams.slug][eq]=natus-vincere"),
    ("hltv results", "https://www.hltv.org/results?team=4608"),
    ("hltv rss", "https://www.hltv.org/rss/news"),
    ("cybersport rss", "https://www.cybersport.ru/rss/materials"),
]:
    st, b = get(url, {"Accept": "application/json, text/html, */*"})
    log(name, st, b, 500)

os.makedirs("probe", exist_ok=True)
open("probe/out.txt", "w").write("\n".join(out))
