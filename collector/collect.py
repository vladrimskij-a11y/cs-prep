#!/usr/bin/env python3
"""
Сборщик данных для CS Prep (Telegram Mini App для комментатора).

Что делает за один запуск:
  1. Берёт ближайшие матчи CS2 тир-1 (S и A) и составы из PandaScore.
  2. Для матчей в ближайшие DETAIL_HOURS часов собирает из bo3.gg
     форму команд, личные встречи и статистику по картам (со счётом по раундам).
  3. Ищет новости про игроков и команды за NEWS_DAYS дней
     (Google News на русском и английском + RSS HLTV и Cybersport.ru),
     отсеивает лишнее по правилам и переводит заголовки на русский.
  4. Складывает всё в docs/data/*.json — их читает Mini App.

Работает только на стандартной библиотеке Python, ничего ставить не нужно.

Переменные окружения:
  PANDASCORE_TOKEN  — токен PandaScore (обязателен)
  TIERS             — уровни турниров, по умолчанию "s,a"
  DAYS_AHEAD        — на сколько дней вперёд показывать матчи (7)
  DETAIL_HOURS      — для матчей в ближайшие N часов собирать подробности (72)
  NEWS_DAYS         — глубина новостей в днях (7)
"""

from __future__ import annotations

import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
CACHE = ROOT / "cache"

PANDA = "https://api.pandascore.co"
BO3 = "https://api.bo3.gg/api/v1"
TOKEN = os.environ.get("PANDASCORE_TOKEN", "").strip()
TIERS = {t.strip().lower() for t in os.environ.get("TIERS", "s,a").split(",") if t.strip()}
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "7"))
DETAIL_HOURS = int(os.environ.get("DETAIL_HOURS", "72"))
NEWS_DAYS = int(os.environ.get("NEWS_DAYS", "7"))
MAPS_WINDOW_DAYS = 120

# Ленты профильных сайтов. Любую можно убрать или добавить свою.
RSS_FEEDS = [
    ("HLTV", "https://www.hltv.org/rss/news"),
    ("Cybersport.ru", "https://www.cybersport.ru/rss/materials"),
]

UA = "Mozilla/5.0 (compatible; cs-prep-bot/1.0; +https://github.com)"
NOW = datetime.now(timezone.utc)

stats = {"panda_requests": 0, "bo3_requests": 0, "liquipedia_requests": 0, "translations": 0, "warnings": []}


def warn(msg: str) -> None:
    print("WARN:", msg, file=sys.stderr)
    if len(stats["warnings"]) < 40:
        stats["warnings"].append(msg)


# ---------------------------------------------------------------- HTTP

def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                last = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


class PandaForbidden(Exception):
    """Эндпоинт недоступен на текущем тарифе."""


def panda(path: str, params: dict | None = None):
    q = urllib.parse.urlencode(params or {}, safe="[],:")
    url = f"{PANDA}{path}" + (f"?{q}" if q else "")
    stats["panda_requests"] += 1
    try:
        raw = http_get(url, {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PandaForbidden(f"{e.code} {path}") from e
        raise
    return json.loads(raw)


def bo3(path: str, params: dict | None = None):
    q = urllib.parse.urlencode(params or {})
    url = f"{BO3}{path}" + (f"?{q}" if q else "")
    stats["bo3_requests"] += 1
    time.sleep(0.25)
    return json.loads(http_get(url, {"Accept": "application/json"}))


# ---------------------------------------------------------------- cache

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        txt = json.dumps(data, ensure_ascii=False, indent=1)
    else:
        txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(txt, "utf-8")


GAMES_CACHE_F = CACHE / "bo3_games.json"     # карты по завершённым матчам bo3 (не меняются)
TEAMS_CACHE_F = CACHE / "bo3_teams.json"     # PandaScore id → bo3 id
TR_CACHE_F = CACHE / "translations.json"     # переводы заголовков
ROSTER_CACHE_F = CACHE / "liquipedia_rosters.json"  # тренер и роли игроков с Liquipedia
games_cache: dict = load_json(GAMES_CACHE_F, {})
teams_map: dict = load_json(TEAMS_CACHE_F, {})
tr_cache: dict = load_json(TR_CACHE_F, {})
roster_cache: dict = load_json(ROSTER_CACHE_F, {})


# ---------------------------------------------------------------- helpers

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9а-я]", "", (s or "").lower())


def team_brief(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "acronym": t.get("acronym"),
        "slug": t.get("slug"),
        "image": t.get("image_url"),
        "location": t.get("location"),
    }


def tournament_label(m: dict) -> str:
    league = (m.get("league") or {}).get("name") or ""
    serie = (m.get("serie") or {}).get("full_name") or (m.get("serie") or {}).get("name") or ""
    return " ".join(p for p in (league, serie) if p).strip()


def stage_label(m: dict) -> str:
    name = m.get("name") or ""
    stage = name.split(":")[0].strip() if ":" in name else ""
    group = (m.get("tournament") or {}).get("name") or ""
    return " · ".join(p for p in (group, stage) if p)


def match_brief(m: dict) -> dict:
    opps = [team_brief(o["opponent"]) for o in m.get("opponents") or [] if o.get("opponent")]
    streams = m.get("streams_list") or []
    main = next((s for s in streams if s.get("main")), streams[0] if streams else None)
    ru = next((s for s in streams if (s.get("language") or "").lower() == "ru"), None)
    return {
        "id": m["id"],
        "begin_at": m.get("begin_at") or m.get("scheduled_at"),
        "status": m.get("status"),
        "bo": m.get("number_of_games"),
        "name": m.get("name"),
        "tournament": tournament_label(m),
        "stage": stage_label(m),
        "tier": ((m.get("tournament") or {}).get("tier") or "").lower(),
        "serie": {
            "id": (m.get("serie") or {}).get("id"),
            "begin": (m.get("serie") or {}).get("begin_at"),
            "end": (m.get("serie") or {}).get("end_at"),
            "image": (m.get("league") or {}).get("image_url"),
            "prize": (m.get("tournament") or {}).get("prizepool"),
        },
        "teams": opps,
        "stream": (ru or main or {}).get("raw_url"),
    }


# ---------------------------------------------------------------- PandaScore: матчи и составы

def fetch_upcoming() -> list[dict]:
    end = NOW + timedelta(days=DAYS_AHEAD)
    out: dict[int, dict] = {}
    for path in ("/csgo/matches/running", "/csgo/matches/upcoming"):
        for page in (1, 2, 3):
            params = {"sort": "begin_at", "per_page": 100, "page": page}
            if path.endswith("upcoming"):
                params["range[begin_at]"] = f"{iso(NOW - timedelta(hours=6))},{iso(end)}"
            try:
                batch = panda(path, params)
            except PandaForbidden as e:
                warn(f"Нет доступа к {path}: {e}")
                break
            for m in batch:
                tier = ((m.get("tournament") or {}).get("tier") or "").lower()
                if TIERS and tier not in TIERS:
                    continue
                out[m["id"]] = m
            if len(batch) < 100:
                break
    res = sorted(out.values(), key=lambda m: m.get("begin_at") or m.get("scheduled_at") or "")
    print(f"Матчей тир-{'/'.join(sorted(TIERS)).upper()}: {len(res)}")
    return res


_team_cache: dict[int, dict] = {}


def fetch_team(team_id: int) -> dict:
    if team_id not in _team_cache:
        try:
            _team_cache[team_id] = panda(f"/teams/{team_id}")
        except Exception as e:
            warn(f"Команда {team_id}: {e}")
            _team_cache[team_id] = {}
    return _team_cache[team_id]


def player_brief(p: dict) -> dict:
    age = p.get("age")
    if not age and p.get("birthday"):
        bd = parse_iso(p["birthday"][:10] + "T00:00:00Z")
        if bd:
            age = int((NOW - bd).days // 365.25)
    return {
        "id": p.get("id"),
        "nick": p.get("name"),
        "first_name": p.get("first_name"),
        "last_name": p.get("last_name"),
        "nationality": p.get("nationality"),
        "age": age,
        "role": p.get("role"),
        "image": p.get("image_url"),
    }



# ---------------------------------------------------------------- Liquipedia: тренер и роли

LP_API = "https://liquipedia.net/counterstrike/api.php"
LP_UA = "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)"
ROSTER_TTL_DAYS = 3
_lp_last = [0.0]


def lp_pages(titles: list[str]) -> dict[str, str]:
    """Вики-текст страниц Liquipedia: {запрошенное название: текст}. Не чаще запроса в 2,5 с (правила Liquipedia)."""
    wait = 2.5 - (time.time() - _lp_last[0])
    if wait > 0:
        time.sleep(wait)
    _lp_last[0] = time.time()
    stats["liquipedia_requests"] += 1
    url = LP_API + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "titles": "|".join(titles), "redirects": 1, "format": "json"})
    data = json.loads(http_get(url, {"User-Agent": LP_UA, "Accept": "application/json"}))
    q = data.get("query") or {}
    alias = {}
    for k in ("normalized", "redirects"):
        for r in q.get(k) or []:
            alias[r["from"]] = r["to"]
    by_title = {}
    for pg in (q.get("pages") or {}).values():
        try:
            by_title[pg["title"]] = pg["revisions"][0]["slots"]["main"]["*"]
        except (KeyError, IndexError):
            pass
    out = {}
    for t in titles:
        cur, seen = t, set()
        while cur in alias and cur not in seen:
            seen.add(cur)
            cur = alias[cur]
        if cur in by_title:
            out[t] = by_title[cur]
    return out


def lp_search(name: str) -> str | None:
    """Поиск страницы команды по названию (The Mongolz → The MongolZ, 9z → 9z Team)."""
    wait = 2.5 - (time.time() - _lp_last[0])
    if wait > 0:
        time.sleep(wait)
    _lp_last[0] = time.time()
    stats["liquipedia_requests"] += 1
    url = LP_API + "?" + urllib.parse.urlencode({"action": "opensearch", "search": name, "limit": 5, "format": "json"})
    try:
        res = json.loads(http_get(url, {"User-Agent": LP_UA, "Accept": "application/json"}))
    except Exception as e:
        warn(f"Liquipedia поиск {name}: {e!r}")
        return None
    for title in (res[1] if len(res) > 1 else []):
        if "/" not in title and "academy" not in title.lower():
            return title
    return None


def lp_param(line: str, key: str) -> str | None:
    m = re.search(r"\|\s*" + key + r"\s*=\s*([^|}<\n]*)", line)
    return m.group(1).strip() if m and m.group(1).strip() else None


def player_roles(wikitext: str) -> tuple[list[str], bool]:
    """Текущие роли из инфобокса игрока (roles=igl,entry / role=…). Возвращает (роли, igl)."""
    box = wikitext[: wikitext.find("\n}}") + 3] if "\n}}" in wikitext else wikitext[:4000]
    raw: list[str] = []
    for m in re.finditer(r"\|\s*roles?\d?\s*=\s*([^\n|}]*)", box, re.IGNORECASE):
        raw += [x.strip().lower() for x in re.split(r"[,/;]", m.group(1)) if x.strip()]
    igl = any(re.search(r"\bigl\b|leader", r) for r in raw)
    awp = any(re.search(r"awp|sniper", r) for r in raw)
    rifle_kind = None
    for rx, ru in ((r"entry", "энтри"), (r"lurk", "люркер"), (r"support", "саппорт"), (r"anchor", "опорник")):
        if any(re.search(rx, r) for r in raw):
            rifle_kind = ru
            break
    rifle = rifle_kind is not None or any(re.search(r"rifl", r) for r in raw)
    res = []
    if awp and rifle:
        res.append("Рифл + AWP")
    elif awp:
        res.append("Снайпер")
    elif rifle:
        res.append("Рифлер" + (f" · {rifle_kind}" if rifle_kind else ""))
    return res, igl


def lp_roster(t: dict) -> dict | None:
    """Активный состав с Liquipedia: тренер, капитан, роли. Кэш на ROSTER_TTL_DAYS дней."""
    key = str(t["id"])
    cached = roster_cache.get(key)
    if cached and (parse_iso(cached.get("t")) or NOW) > NOW - timedelta(days=ROSTER_TTL_DAYS):
        return cached.get("data")
    names = []
    for nm in (t.get("name"), re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.get("name") or ""), t.get("acronym")):
        if nm and nm not in names:
            names.append(nm)
    data = None
    try:
        pages = lp_pages(names)
        text = next((pages[n] for n in names if n in pages and "Squad|status=active" in pages[n]), None)
        if text is None:
            found = lp_search(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.get("name") or ""))
            if found:
                text = lp_pages([found]).get(found)
                if text and "Squad|status=active" not in text:
                    text = None
        if text is None:
            warn(f"Liquipedia: нет страницы команды {t.get('name')}")
        else:
            i = text.find("Squad|status=active")
            block = text[i: text.find("\n}}", i)]
            people = []
            for line in block.split("\n"):
                if "{{Person" not in line:
                    continue
                pid = lp_param(line, "id")
                if not pid:
                    continue
                role = (lp_param(line, "role") or "").lower()
                people.append({
                    "nick": pid,
                    "page": lp_param(line, "link") or pid,
                    "name": lp_param(line, "name"),
                    "flag": (lp_param(line, "flag") or "").upper()[:2] or None,
                    "igl": lp_param(line, "igl") == "y",
                    "coach": "coach" in role,
                    "note": None if "coach" in role or not role else role,
                })
            players = [p for p in people if not p["coach"]]
            ppages = lp_pages([p["page"] for p in players]) if players else {}
            any_igl = any(p["igl"] for p in players)
            for p in players:
                roles, igl = player_roles(ppages.get(p["page"], ""))
                p["roles"] = roles
                if not any_igl and igl:
                    p["igl"] = True  # в составе капитан не отмечен — берём со страницы игрока
            data = {
                "coach": [{"nick": p["nick"], "name": p["name"], "flag": p["flag"]} for p in people if p["coach"]],
                "players": [{k: p[k] for k in ("nick", "name", "flag", "igl", "roles", "note")} for p in players],
            }
    except Exception as e:
        warn(f"Liquipedia {t.get('name')}: {e!r}")
        return cached.get("data") if cached else None
    roster_cache[key] = {"t": iso(NOW), "data": data}
    return data


def merge_roster(panda_players: list[dict], lp: dict | None) -> tuple[list[dict], list[dict]]:
    """Состав: берём активный состав Liquipedia (он точнее), дополняем данными PandaScore."""
    if not lp or not lp.get("players"):
        return panda_players, []
    by_nick = {norm(p.get("nick")): p for p in panda_players}
    res = []
    for lp_p in lp["players"]:
        pp = by_nick.get(norm(lp_p["nick"]), {})
        tags = list(dict.fromkeys((["Капитан"] if lp_p.get("igl") else []) + list(lp_p.get("roles") or [])))
        if lp_p.get("note") and lp_p["note"] not in ("player",):
            tags.append({"stand-in": "Замена", "loan": "В аренде", "inactive": "Неактивен"}.get(lp_p["note"], lp_p["note"]))
        first, last = pp.get("first_name"), pp.get("last_name")
        if not (first or last) and lp_p.get("name"):
            first, last = lp_p["name"], None
        res.append({
            "id": pp.get("id"),
            "nick": lp_p["nick"],
            "first_name": first,
            "last_name": last,
            "nationality": pp.get("nationality") or lp_p.get("flag"),
            "age": pp.get("age"),
            "image": pp.get("image"),
            "tags": tags,
        })
    return res, lp.get("coach") or []

# ---------------------------------------------------------------- bo3.gg: история, карты

def bo3_team_id(t: dict) -> int | None:
    """Ищет команду PandaScore на bo3.gg: по ps_id, слагу, названию."""
    key = str(t["id"])
    if teams_map.get(key):
        return teams_map[key]
    found = None
    tries = []
    if t.get("slug"):
        tries.append({"filter[teams.slug][eq]": t["slug"]})
    if t.get("name"):
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t["name"])  # TheMongolz → The Mongolz
        for nm in dict.fromkeys([t["name"], spaced]):
            tries.append({"filter[teams.name][eq]": nm})
            tries.append({"filter[teams.slug][eq]": re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")})
    if t.get("acronym"):
        tries.append({"filter[teams.acronym][eq]": t["acronym"]})
    for params in tries:
        try:
            res = (bo3("/teams", params).get("results") or [])
        except Exception as e:
            warn(f"bo3 поиск {t.get('name')}: {e}")
            continue
        exact = [r for r in res if r.get("ps_id") == t["id"]]
        pick = exact[0] if exact else (res[0] if len(res) == 1 else None)
        if pick:
            found = pick["id"]
            break
    if found is None:
        warn(f"bo3: не нашёл команду {t.get('name')}")
    else:
        teams_map[key] = found
    return found


_bo3_hist: dict[int, list] = {}


def bo3_history(bid: int, pages: int = 2) -> list[dict]:
    if bid in _bo3_hist:
        return _bo3_hist[bid]
    res: list[dict] = []
    for page in range(pages):
        try:
            j = bo3("/matches", {
                "page[limit]": 50, "page[offset]": page * 50, "sort": "-start_date",
                "filter[matches.status][in]": "finished",
                "filter[matches.team_ids][overlap]": bid,
                "with": "teams,tournament",
            })
        except Exception as e:
            warn(f"bo3 история {bid}: {e}")
            break
        batch = j.get("results") or []
        res.extend(batch)
        if len(batch) < 50:
            break
    _bo3_hist[bid] = res
    return res


MAP_NAMES = {"dust2": "Dust2", "de_dust2": "Dust2"}


def map_title(raw: str | None) -> str | None:
    if not raw:
        return None
    r = raw.lower()
    if r in MAP_NAMES:
        return MAP_NAMES[r]
    r = r.replace("de_", "").replace("cs_", "")
    return r[:1].upper() + r[1:]


def bo3_games(m: dict) -> list[dict]:
    """Карты матча: [{'map','winner':1|2,'score':'13:10'}] в порядке игры. Кэшируется навсегда."""
    key = str(m["id"])
    if key in games_cache:
        return games_cache[key]
    try:
        games = bo3("/games", {"filter[games.match_id][eq]": m["id"]}).get("results") or []
    except Exception as e:
        warn(f"bo3 карты {m['id']}: {e}")
        return []
    t1, t2 = m.get("team1") or {}, m.get("team2") or {}
    n1 = {norm(t1.get("name")), norm(t1.get("slug")), norm(t1.get("acronym"))} - {""}
    n2 = {norm(t2.get("name")), norm(t2.get("slug")), norm(t2.get("acronym"))} - {""}
    ms = m.get("maps_score") or []
    res = []
    for g in sorted(games, key=lambda g: g.get("number") or 0):
        if g.get("status") != "finished":
            continue
        w = norm(g.get("winner_clan_name"))
        winner = 1 if w in n1 else 2 if w in n2 else None
        if winner is None:
            i = (g.get("number") or 0) - 1
            if 0 <= i < len(ms):
                winner = 1 if ms[i] else 2
        ws, ls = g.get("winner_clan_score"), g.get("loser_clan_score")
        score = None
        if ws is not None and ls is not None and winner:
            score = f"{ws}:{ls}" if winner == 1 else f"{ls}:{ws}"
        res.append({"map": map_title(g.get("map_name")), "winner": winner, "score": score})
    if m.get("status") == "finished":
        games_cache[key] = res
    return res


def side_of(m: dict, bid: int) -> int:
    return 1 if m.get("team1_id") == bid else 2


def opp_of(m: dict, bid: int) -> dict:
    return (m.get("team2") if m.get("team1_id") == bid else m.get("team1")) or {}


def tour_name(m: dict) -> str:
    return ((m.get("tournament") or {}).get("name") or "").strip()


def from_side(score: str | None, side: int) -> str | None:
    """Счёт по раундам с точки зрения команды на стороне side."""
    if not score:
        return None
    a, b = score.split(":")
    return score if side == 1 else f"{b}:{a}"


def build_form(bid: int, hist: list[dict], n: int = 10) -> list[dict]:
    res = []
    for m in hist[:n]:
        s = side_of(m, bid)
        my = m.get("team1_score") if s == 1 else m.get("team2_score")
        their = m.get("team2_score") if s == 1 else m.get("team1_score")
        opp = opp_of(m, bid)
        res.append({
            "date": m.get("start_date"),
            "opponent": opp.get("acronym") or opp.get("name"),
            "opponent_name": opp.get("name"),
            "score": my, "opp_score": their,
            "won": m.get("winner_team_id") == bid,
            "tournament": tour_name(m),
            "tier": (m.get("tier") or "").lower(),
        })
    return res


def build_h2h(a: int, b: int, hist_a: list[dict]) -> list[dict]:
    since = NOW - timedelta(days=365)
    res = []
    for m in hist_a:
        if {m.get("team1_id"), m.get("team2_id")} != {a, b}:
            continue
        d = parse_iso(m.get("start_date"))
        if d and d < since:
            break
        sa = side_of(m, a)
        maps = []
        for g in bo3_games(m):
            w = None if not g["winner"] else (0 if g["winner"] == sa else 1)
            maps.append({"map": g["map"], "winner": w, "score": from_side(g["score"], sa)})
        res.append({
            "date": m.get("start_date"),
            "tournament": tour_name(m),
            "bo": m.get("bo_type"),
            "score": [m.get("team1_score") if sa == 1 else m.get("team2_score"),
                      m.get("team2_score") if sa == 1 else m.get("team1_score")],
            "winner": 0 if m.get("winner_team_id") == a else 1 if m.get("winner_team_id") == b else None,
            "maps": maps,
        })
    return res


def build_maps(bids: list[int | None], hists: list[list[dict]], max_matches: int = 40) -> dict:
    since = NOW - timedelta(days=MAPS_WINDOW_DAYS)
    table: dict[str, dict] = {}
    recent: dict[str, list] = {}
    for idx, (bid, hist) in enumerate(zip(bids, hists)):
        if not bid:
            continue
        for m in hist[:max_matches]:
            d = parse_iso(m.get("start_date"))
            if d and d < since:
                break
            s = side_of(m, bid)
            opp = opp_of(m, bid)
            for g in bo3_games(m):
                name = g.get("map")
                if not name or not g.get("winner"):
                    continue
                row = table.setdefault(name, {"map": name, "teams": [{"played": 0, "won": 0}, {"played": 0, "won": 0}]})
                won = g["winner"] == s
                row["teams"][idx]["played"] += 1
                row["teams"][idx]["won"] += int(won)
                recent.setdefault(name, []).append({
                    "team": idx, "date": m.get("start_date"), "vs": opp.get("acronym") or opp.get("name"),
                    "won": won, "score": from_side(g.get("score"), s), "tournament": tour_name(m)})
    for name in recent:
        recent[name].sort(key=lambda r: r["date"] or "", reverse=True)
    rows = sorted(table.values(), key=lambda r: -(r["teams"][0]["played"] + r["teams"][1]["played"]))
    return {"available": bool(rows), "window_days": MAPS_WINDOW_DAYS, "table": rows, "recent": recent}


# ---------------------------------------------------------------- новости

def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return parse_iso(s)


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()


def parse_rss(raw: bytes, default_source: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = strip_html(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else default_source
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        items.append({
            "title": title,
            "url": link,
            "source": source,
            "date": parse_date(it.findtext("pubDate")),
            "snippet": strip_html(it.findtext("description") or "")[:400],
        })
    return items


_feed_cache: dict[str, list] = {}


def fetch_feed(url: str, source: str) -> list[dict]:
    if url in _feed_cache:
        return _feed_cache[url]
    try:
        items = parse_rss(http_get(url, timeout=20), source)
    except Exception as e:
        warn(f"Лента {source}: {e}")
        items = []
    since = NOW - timedelta(days=NEWS_DAYS)
    items = [i for i in items if i["date"] and i["date"] >= since and i["title"]]
    _feed_cache[url] = items
    time.sleep(0.4)
    return items


def google_news(query: str, lang: str) -> list[dict]:
    q = f"{query} when:{NEWS_DAYS}d"
    if lang == "ru":
        params = {"q": q, "hl": "ru", "gl": "RU", "ceid": "RU:ru"}
    else:
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return fetch_feed("https://news.google.com/rss/search?" + urllib.parse.urlencode(params), "Google News")


CS_CONTEXT = re.compile(
    r"\bCS ?2\b|\bCS:?GO\b|Counter[- ]Strike|\bКС ?2?\b|\bHLTV\b|\bIEM\b|\bBLAST\b|\bPGL\b|\bESL\b|\bmajor\b|мейджор"
    r"|\bAWP\b|\bIGL\b|капитан|снайпер|roster|состав|transfer|трансфер",
    re.IGNORECASE)
CS_SOURCES = {"hltv", "cybersport.ru", "dust2.us", "dust2.com.br", "bo3.gg", "esports.gg", "dexerto", "egamersworld",
              "escorenews", "cyber.sports.ru", "sports.ru", "championat", "offstage.ru", "vpesports", "esportsinsider",
              "hotspawn", "dust2", "gamespot", "pcgamer", "esports.net", "thespike", "gosugamers", "liquipedia"}
OTHER_GAMES = re.compile(
    r"\b(MLBB|Mobile Legends|Dota|Valorant|League of Legends|\bLoL\b|PUBG|Fortnite|Apex|Rainbow Six|\bR6\b|Overwatch"
    r"|Rocket League|Call of Duty|\bCoD\b|Standoff|Deadlock|Marvel Rivals|EA FC|FIFA|Free Fire|StarCraft|Tekken)\b",
    re.IGNORECASE)
SPAM = re.compile(
    r"prediction|predictions|прогноз|ставк|коэффициент|\bodds\b|betting|букмекер|live stream|how to watch|где смотреть"
    r"|расписание|schedule|^Матч .+ · |\bvs\.? .+ \d{1,2} (ноября|октября|декабря)",
    re.IGNORECASE)
COMMON_WORDS = {"try", "zone", "ice", "fire", "max", "flash", "ace", "one", "jack", "king", "magic", "hero", "legacy",
                "spirit", "liquid", "heroic", "complexity", "falcons", "aurora", "monte", "rare", "nemiga", "apeks",
                "sky", "fame", "mir", "boss", "chopper", "hunter", "techno", "snow", "smooya", "ex", "it", "im", "kyxsan"}

KIND_RULES = [
    ("интервью", r"interview|интервью|told|said|says|рассказал|заявил|признался|ответил|высказал|\""),
    ("трансфер", r"sign|signs|signed|join|joins|leave|leaves|bench|benched|transfer|roster|подписал|перешёл|перешел|покинул|скамейк|замен|трансфер|состав"),
    ("соцсети", r"tweet|twitter|\bX\b|post|stream|clip|reddit|instagram|соцсет|стрим|твит|пост|клип|ролик"),
    ("слухи", r"rumou?r|report:|reportedly|insider|слух|инсайд|по данным"),
    ("результат", r"beat|beats|defeat|win|wins|won|eliminat|advance|upset|обыграл|победил|вылетел|прош[её]л|разгромил|проиграл"),
]


def classify(title: str) -> str:
    for kind, rx in KIND_RULES:
        if re.search(rx, title, re.IGNORECASE):
            return kind
    return "новость"


def entity_re(word: str, case: bool) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(word) + r"(?![\w])", 0 if case else re.IGNORECASE)


def is_ambiguous(nick: str) -> bool:
    n = nick.lower()
    return len(nick) <= 3 or n in COMMON_WORDS or nick.isdigit() or (nick.islower() and nick.isalpha() and len(nick) <= 5)


def item_key(it: dict) -> str:
    n = re.sub(r"\W+", "", it["title"].lower())[:120]
    return hashlib.sha1(n.encode()).hexdigest()[:16]


def has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[а-яё]", s, re.IGNORECASE))


LANG_RU = {"en": "английского", "pt": "португальского", "es": "испанского", "de": "немецкого", "fr": "французского",
           "pl": "польского", "uk": "украинского", "tr": "турецкого", "da": "датского", "sv": "шведского", "fi": "финского",
           "cs": "чешского", "sk": "словацкого", "zh-CN": "китайского", "zh": "китайского", "ko": "корейского",
           "ja": "японского", "it": "итальянского", "nl": "нидерландского", "no": "норвежского", "ro": "румынского",
           "hu": "венгерского", "lt": "литовского", "lv": "латышского", "et": "эстонского", "kk": "казахского",
           "mn": "монгольского", "be": "белорусского", "bg": "болгарского", "sr": "сербского", "hr": "хорватского",
           "id": "индонезийского", "vi": "вьетнамского", "th": "тайского", "ar": "арабского", "he": "иврита"}


def _gtx(text: str) -> tuple[str, str] | None:
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t", "q": text})
    data = json.loads(http_get(url, timeout=20))
    ru = "".join(part[0] for part in data[0] if part and part[0]).strip()
    lang = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
    stats["translations"] += 1
    time.sleep(0.3)
    return ru, lang


def translate_ex(text: str) -> tuple[str | None, str | None]:
    """(перевод на русский или None, если уже русский; код исходного языка)."""
    if not text:
        return None, None
    if has_cyrillic(text) and not re.search(r"[іїєґў]", text, re.IGNORECASE):
        return None, "ru"
    k = hashlib.sha1(text.encode()).hexdigest()[:16]
    c = tr_cache.get(k)
    if c and "lang" in c:
        return c["ru"], c["lang"]
    try:
        parts, lang = [], ""
        # длинный текст переводим кусками по предложениям
        chunk = ""
        for sent in re.split(r"(?<=[.!?])\s+", text):
            if len(chunk) + len(sent) > 1500 and chunk:
                r = _gtx(chunk)
                parts.append(r[0]); lang = lang or r[1]
                chunk = ""
            chunk += (" " if chunk else "") + sent
        if chunk:
            r = _gtx(chunk)
            parts.append(r[0]); lang = lang or r[1]
        ru = " ".join(parts).strip()
    except Exception as e:
        warn(f"Перевод: {e!r}")
        return None, None
    if lang == "ru":
        ru = None
    tr_cache[k] = {"ru": ru, "lang": lang, "t": iso(NOW)}
    return ru, lang


def translate(text: str) -> str | None:
    return translate_ex(text)[0]


# ---------------------------------------------------------------- статья: ссылка на оригинал и описание

ART_CACHE_F = CACHE / "articles.json"
art_cache: dict = load_json(ART_CACHE_F, {})
ART_NEW_LIMIT = 150          # сколько новых статей обрабатывать за запуск
_art_new = [0]


def gnews_decode(url: str) -> str | None:
    """Достаёт настоящую ссылку на статью из ссылки Google News."""
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([^?/]+)", url)
    if not m:
        return url
    gid = m.group(1)
    try:
        html = http_get(f"https://news.google.com/articles/{gid}?hl=en-US&gl=US&ceid=US:en", timeout=20).decode("utf-8", "replace")
        sg = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not (sg and ts):
            return None
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (gid, ts.group(1), sg.group(1)))
        body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner]]]))
        req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body.encode(),
                                     method="POST", headers={"User-Agent": UA,
                                                             "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "replace")
        for chunk in txt.split("\n"):
            chunk = chunk.strip()
            if not chunk.startswith("[["):
                continue
            try:
                arr = json.loads(chunk)
                for row in arr:
                    if isinstance(row, list) and len(row) > 2 and row[1] == "Fbv4je" and row[2]:
                        res = json.loads(row[2])
                        if isinstance(res, list) and len(res) > 1 and str(res[1]).startswith("http"):
                            return res[1]
            except Exception:
                continue
    except Exception as e:
        warn(f"Google News ссылка: {e!r}")
    return None


def _meta(html: str, *names: str) -> str | None:
    for n in names:
        for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)["\']' % re.escape(n),
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(n)):
            m = re.search(pat, html, re.IGNORECASE)
            if m and m.group(1).strip():
                return strip_html(m.group(1))
    return None


def article_lead(url: str) -> dict:
    """Описание статьи, которое сайт сам отдаёт для превью (og:description), и картинка."""
    try:
        raw = http_get(url, {"Accept": "text/html,application/xhtml+xml"}, timeout=20)[:600_000]
        html = raw.decode("utf-8", "replace")
    except Exception as e:
        return {"error": str(e)[:80]}
    lead = _meta(html, "og:description", "twitter:description", "description")
    if lead and len(lead) < 60:
        lead = None
    if not lead:
        for m in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL):
            t = strip_html(m.group(1))
            if len(t) > 80:
                lead = t
                break
    if lead and len(lead) > 600:
        lead = lead[:600].rsplit(" ", 1)[0] + "…"
    return {"lead": lead, "image": _meta(html, "og:image", "twitter:image")}


def enrich_article(it: dict) -> dict:
    k = it["key"]
    if k in art_cache:
        return art_cache[k]
    if _art_new[0] >= ART_NEW_LIMIT:
        return {}
    _art_new[0] += 1
    real = gnews_decode(it["url"]) if "news.google.com" in (it["url"] or "") else it["url"]
    info: dict = {"url": real}
    if real:
        info.update(article_lead(real))
        time.sleep(0.3)
    if not info.get("lead") and it.get("snippet") and norm(it["snippet"]) != norm(it["title"]) and len(it["snippet"]) > 60:
        info["lead"] = it["snippet"][:600]
    if info.get("lead"):
        ru, lang = translate_ex(info["lead"])
        info["lead_ru"], info["lead_lang"] = ru, lang
    info["t"] = iso(NOW)
    art_cache[k] = info
    return info


def gather_news(teams: list[dict]) -> list[dict]:
    """Новости про игроков и команды: поиск, отбор по правилам, перевод."""
    entities = []  # (метка, regex, неоднозначный ли, индекс команды)
    for ti, t in enumerate(teams):
        for label in {t.get("name"), t.get("acronym")} - {None, ""}:
            amb = label.lower() in COMMON_WORDS or len(label) <= 3
            entities.append((t["name"], entity_re(label, case=amb), amb, ti))
        for p in t.get("players") or []:
            nick = p.get("nick") or ""
            if len(nick) < 2:
                continue
            amb = is_ambiguous(nick)
            entities.append((nick, entity_re(nick, case=amb), amb, ti))

    pool: list[dict] = []
    cs = '(CS2 OR "Counter-Strike")'
    for t in teams:
        for lang in ("ru", "en"):
            pool += google_news(f'"{t["name"]}" {cs}', lang)
        for p in t.get("players") or []:
            if p.get("nick") and len(p["nick"]) >= 2:
                for lang in ("ru", "en"):
                    pool += google_news(f'"{p["nick"]}" {cs}', lang)
    for source, url in RSS_FEEDS:
        pool += fetch_feed(url, source)

    team_names = [t["name"] for t in teams]
    found: dict[str, dict] = {}
    for it in pool:
        title, text = it["title"], f"{it['title']} {it['snippet']}"
        if SPAM.search(title):
            continue
        if OTHER_GAMES.search(title) and not CS_CONTEXT.search(title):
            continue
        src = (it["source"] or "").lower()
        cs_src = any(s in src for s in CS_SOURCES)
        about = []
        for label, rx, amb, ti in entities:
            if not rx.search(title):
                continue
            if amb:
                # неоднозначный ник/название: нужно, чтобы рядом была его команда или явный контекст CS
                own_team = teams[ti]["name"]
                if not (entity_re(own_team, False).search(text) and label != own_team) and not CS_CONTEXT.search(title):
                    continue
            if label not in about:
                about.append(label)
        if not about:
            continue
        if not (cs_src or CS_CONTEXT.search(text) or any(n in about for n in team_names)):
            continue
        k = item_key(it)
        cur = found.get(k)
        if cur:
            for a in about:
                if a not in cur["about"]:
                    cur["about"].append(a)
            continue
        found[k] = {**it, "about": about, "key": k}

    items = sorted(found.values(), key=lambda i: i["date"], reverse=True)[:60]
    out = []
    for it in items:
        ru, lang = translate_ex(it["title"])
        art = enrich_article(it)
        lead_lang = art.get("lead_lang")
        src_lang = lead_lang or lang
        out.append({
            "key": it["key"],
            "title": it["title"],
            "title_ru": ru,
            "lang": src_lang,
            "lang_ru": LANG_RU.get(src_lang or "", src_lang) if src_lang and src_lang != "ru" else None,
            "lead": art.get("lead"),
            "lead_ru": art.get("lead_ru"),
            "image": art.get("image"),
            "kind": classify(it["title"]),
            "about": it["about"],
            "source": it["source"],
            "url": art.get("url") or it["url"],
            "date": iso(it["date"]),
        })
    return out


def build_news(teams: list[dict]) -> dict:
    return {"updated": iso(NOW), "days": NEWS_DAYS, "items": gather_news(teams)}


# ---------------------------------------------------------------- main

def build_detail(m: dict, prev: dict | None) -> dict:
    brief = match_brief(m)
    teams, bids, hists = [], [], []
    for t in brief["teams"]:
        full = fetch_team(t["id"])
        players = [player_brief(p) for p in full.get("players") or [] if p.get("active", True)]
        players, coach = merge_roster(players, lp_roster({**t, "name": full.get("name") or t.get("name")}))
        bid = bo3_team_id({**t, "slug": full.get("slug") or t.get("slug")})
        hist = bo3_history(bid) if bid else []
        bids.append(bid)
        hists.append(hist)
        teams.append({**t, "players": players, "coach": coach, "form": build_form(bid, hist) if bid else []})
    detail = {**brief, "teams": teams, "updated": iso(NOW)}
    if len(bids) == 2 and all(bids):
        detail["h2h"] = build_h2h(bids[0], bids[1], hists[0])
    else:
        detail["h2h"] = []
    detail["maps"] = build_maps(bids, hists)
    begin = parse_iso(brief["begin_at"])
    soon = begin is not None and begin - NOW <= timedelta(hours=DETAIL_HOURS)
    if soon or not (prev and prev.get("news")):
        detail["news"] = build_news(teams)
    else:
        detail["news"] = prev["news"]
    return detail


def prune_caches() -> None:
    cutoff = iso(NOW - timedelta(days=NEWS_DAYS + 7))
    for k in [k for k, v in tr_cache.items() if (v.get("t") or "") < cutoff]:
        del tr_cache[k]
    if len(games_cache) > 6000:
        for k in sorted(games_cache, key=int)[:-5000]:
            del games_cache[k]


def main() -> int:
    if not TOKEN:
        print("Нет PANDASCORE_TOKEN. Добавь его в Settings → Secrets → Actions.", file=sys.stderr)
        return 1
    matches = fetch_upcoming()
    listing = []
    keep_ids = set()
    for m in matches:
        b = match_brief(m)
        begin = parse_iso(b["begin_at"])
        detailed = len(b["teams"]) == 2 and begin is not None and begin - NOW <= timedelta(hours=DETAIL_HOURS)
        if detailed:
            path = OUT / "match" / f"{m['id']}.json"
            prev = load_json(path, None)
            try:
                print(f"→ {b['name']} ({b['begin_at']})")
                save_json(path, build_detail(m, prev))
                keep_ids.add(m["id"])
            except Exception as e:
                warn(f"Матч {m['id']}: {e!r}")
                detailed = path.exists()
                if detailed:
                    keep_ids.add(m["id"])
        b["has_detail"] = detailed
        listing.append(b)

    for f in (OUT / "match").glob("*.json"):
        if int(f.stem) not in keep_ids:
            f.unlink()

    prune_caches()
    save_json(GAMES_CACHE_F, games_cache)
    save_json(TEAMS_CACHE_F, teams_map)
    save_json(TR_CACHE_F, tr_cache)
    save_json(ROSTER_CACHE_F, roster_cache)
    for k in [k for k, v in art_cache.items() if (v.get("t") or "") < iso(NOW - timedelta(days=NEWS_DAYS + 7))]:
        del art_cache[k]
    save_json(ART_CACHE_F, art_cache)
    tours: dict = {}
    for b in listing:
        sid = str((b.get("serie") or {}).get("id") or re.sub(r"\W+", "-", b["tournament"]).strip("-").lower())
        tr = tours.setdefault(sid, {"id": sid, "name": b["tournament"], "tier": b["tier"], "image": b["serie"].get("image"),
                                    "begin": b["serie"].get("begin"), "end": b["serie"].get("end"),
                                    "prize": b["serie"].get("prize"), "matches": 0, "next": None, "live": 0})
        tr["matches"] += 1
        if b.get("status") == "running":
            tr["live"] += 1
        if b.get("tier") == "s":
            tr["tier"] = "s"
        if not tr["prize"] and b["serie"].get("prize"):
            tr["prize"] = b["serie"]["prize"]
        if b.get("status") != "running" and (tr["next"] is None or (b["begin_at"] or "") < tr["next"]):
            tr["next"] = b["begin_at"]
    tour_list = sorted(tours.values(), key=lambda x: (x["tier"] != "s", x["next"] or "9999"))
    save_json(OUT / "matches.json", {
        "updated": iso(NOW),
        "tiers": sorted(TIERS),
        "tournaments": tour_list,
        "matches": listing,
        "meta": stats,
    }, pretty=True)
    print(f"Готово: {len(listing)} матчей, PandaScore: {stats['panda_requests']}, bo3: {stats['bo3_requests']}, "
          f"переводов: {stats['translations']}, предупреждений: {len(stats['warnings'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
