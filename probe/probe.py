"""Разовая проверка №2: эндпоинты bo3.gg для карт и варианты нейросети."""
import gzip, json, os, urllib.request, urllib.error

UA = "cs-prep-bot/1.0 (github.com/vladrimskij-a11y/cs-prep)"
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


def log(name, st, hd, body, n=900):
    ct = hd.get("Content-Type", "")
    out.append(f"=== {name}: {st} ct={ct} len={len(body)}\n{body[:n]}\n")


tok = os.environ.get("GITHUB_TOKEN", "")
payload = json.dumps({"model": "openai/gpt-4.1-mini", "messages": [{"role": "user", "content": 'Верни JSON {"ok": true}'}],
                      "response_format": {"type": "json_object"}}).encode()
for name, url, extra in [
    ("models new", "https://models.github.ai/inference/chat/completions", {}),
    ("models new + api version", "https://models.github.ai/inference/chat/completions", {"X-GitHub-Api-Version": "2022-11-28"}),
    ("models legacy azure", "https://models.inference.ai.azure.com/chat/completions", {}),
]:
    p = payload if "legacy" not in name else json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": 'Верни JSON {"ok": true}'}]}).encode()
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "application/json"}
    h.update(extra)
    st, hd, b = get(url, h, p, "POST")
    log(name, st, hd, b, 500)

B = "https://api.bo3.gg/api/v1"
for name, url in [
    ("team by ps_id", f"{B}/teams?filter[teams.ps_id][eq]=3216"),
    ("matches team1", f"{B}/matches?page[limit]=3&sort=-start_date&filter[matches.status][in]=finished&filter[matches.team1_id][eq]=787"),
    ("matches any team", f"{B}/matches?page[limit]=3&sort=-start_date&filter[matches.status][in]=finished&filter[matches.team_ids][overlap]=787"),
    ("match show", f"{B}/matches/monte-vs-prodigy-ru-01-11-2026?scope=show-match&with=games"),
    ("games by match", f"{B}/games?filter[games.match_id][eq]=132775"),
]:
    st, hd, b = get(url, {"Accept": "application/json"})
    log(name, st, hd, b)

open("probe/out.txt", "w").write("\n".join(out))
