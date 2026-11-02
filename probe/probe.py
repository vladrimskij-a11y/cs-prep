"""Разовая проверка источников для новых функций. Секреты в вывод не попадают."""
import gzip, json, os, ssl, uuid, urllib.parse, urllib.request, urllib.error

UA = "Mozilla/5.0 (compatible; cs-prep-bot/1.0)"
out = []
def get(url, headers=None, data=None, method=None, ctx=None, raw=False):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b if raw else b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400].decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:300]
def keys(x, depth=0):
    if isinstance(x, dict):
        return {k: (keys(v, depth+1) if depth < 1 and isinstance(v, (dict, list)) else type(v).__name__ if not isinstance(v,(str,int,float,bool)) or v is None else (v if not isinstance(v,str) else v[:40])) for k, v in x.items()}
    if isinstance(x, list):
        return [keys(x[0], depth)] if x else []
    return x
def log(name, st, body, n=1500):
    out.append(f"=== {name}: {st}\n{body[:n] if isinstance(body,str) else body}\n")

B = "https://api.bo3.gg/api/v1"
st, b = get(B + "/matches?" + urllib.parse.urlencode({"page[limit]": 1, "sort": "-start_date", "filter[matches.status][in]": "finished",
           "filter[matches.team_ids][overlap]": 787, "filter[matches.tier][eq]": "s", "with": "teams"}))
m = json.loads(b)["results"][0] if st == 200 else {}
log("match keys", st, json.dumps(keys(m), ensure_ascii=False))
mid, slug = m.get("id"), m.get("slug")
for name, url in [
    ("match show", f"{B}/matches/{slug}?scope=show-match&with=games,teams,tournament,stage,picks,match_maps"),
    ("picks /matches/id/picks", f"{B}/matches/{mid}/picks"),
    ("match_picks filter", f"{B}/match_picks?filter[match_picks.match_id][eq]={mid}"),
    ("picks filter", f"{B}/picks?filter[picks.match_id][eq]={mid}"),
    ("match vetoes", f"{B}/matches/{mid}/vetoes"),
]:
    st, b = get(url)
    try: b = json.dumps(keys(json.loads(b)), ensure_ascii=False)
    except Exception: pass
    log(name, st, b)
st, b = get(f"{B}/games?filter[games.match_id][eq]={mid}")
games = json.loads(b).get("results", []) if st == 200 else []
gid = games[0]["id"] if games else None
out.append(f"game ids: {[g['id'] for g in games]}")
for name, url in [
    ("game show", f"{B}/games/{gid}"),
    ("game with rounds", f"{B}/games/{gid}?with=game_rounds,players_stats,clans"),
    ("game_rounds filter", f"{B}/game_rounds?filter[game_rounds.game_id][eq]={gid}&page[limit]=2"),
    ("rounds", f"{B}/games/{gid}/rounds"),
    ("players stats in game", f"{B}/games/{gid}/players_stats"),
    ("game player stats filter", f"{B}/game_player_stats?filter[game_player_stats.game_id][eq]={gid}&page[limit]=2"),
    ("player by slug", f"{B}/players?filter[players.slug][eq]=m0nesy"),
    ("player general stats", f"{B}/players/m0nesy/general_stats"),
    ("player stats v2", f"https://api.bo3.gg/api/v2/players/m0nesy/stats"),
    ("team stats", f"{B}/teams/natus-vincere/stats"),
    ("team maps stats", f"{B}/teams/natus-vincere/maps_stats"),
]:
    st, b = get(url)
    try: b = json.dumps(keys(json.loads(b)), ensure_ascii=False)
    except Exception: pass
    log(name, st, b, 2500)

# GigaChat
key = os.environ.get("GIGACHAT_KEY", "").strip()
out.append(f"GIGACHAT_KEY set: {bool(key)} len={len(key)}")
st, cert = get("https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt", raw=True)
out.append(f"cert download: {st} len={len(cert) if st==200 else cert}")
ctx = ssl.create_default_context()
if st == 200:
    try: ctx.load_verify_locations(cadata=cert.decode())
    except Exception as e: out.append(f"cert load error {e!r}")
if key:
    st, b = get("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", {"Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()),
               "Content-Type": "application/x-www-form-urlencoded"}, b"scope=GIGACHAT_API_PERS", "POST", ctx)
    tok = None
    if st == 200:
        tok = json.loads(b).get("access_token"); out.append("oauth: 200 ok")
    else:
        log("oauth", st, b, 300)
    if tok:
        st, b = get("https://gigachat.devices.sberbank.ru/api/v1/models", {"Authorization": f"Bearer {tok}"}, ctx=ctx)
        log("models", st, b, 800)
        st, b = get("https://gigachat.devices.sberbank.ru/api/v1/chat/completions", {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                    json.dumps({"model": "GigaChat-2", "messages": [{"role": "user", "content": "Ответь одним словом: столица России?"}]}).encode(), "POST", ctx)
        log("chat", st, b, 500)

# Telegram public channels
for ch in ["durov", "cybersportru", "natus_vincere"]:
    st, b = get(f"https://t.me/s/{ch}", {"Accept": "text/html"})
    out.append(f"t.me/s/{ch}: {st} posts={b.count('tgme_widget_message_text') if st==200 else b[:100]} photos={b.count('tgme_widget_message_photo_wrap') if st==200 else ''}")

# Telegram bot
bt = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
out.append(f"TELEGRAM_BOT_TOKEN set: {bool(bt)}")
if bt:
    st, b = get(f"https://api.telegram.org/bot{bt}/getUpdates")
    try:
        res = json.loads(b).get("result", [])
        chats = {(u.get("message") or {}).get("chat", {}).get("type") for u in res}
        out.append(f"getUpdates: {st} updates={len(res)} chat types={chats}")
    except Exception as e:
        out.append(f"getUpdates: {st} {e!r}")

# Liquipedia bracket check
st, b = get("https://liquipedia.net/counterstrike/api.php?" + urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "Intel Extreme Masters/2026/Beijing", "format": "json"}),
            {"User-Agent": "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"})
if st == 200:
    t = list(json.loads(b)["query"]["pages"].values())[0]["revisions"][0]["slots"]["main"]["*"]
    i = t.find("Bracket")
    out.append("LP bracket ctx: " + t[i-200:i+1500])
open("probe/out.txt", "w").write("\n".join(out))
