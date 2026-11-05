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
  TIERS             — уровни турниров, по умолчанию "s" (только тир-1)
  DAYS_AHEAD        — на сколько дней вперёд показывать матчи (7)
  DETAIL_HOURS      — для матчей в ближайшие N часов собирать подробности (72)
  NEWS_DAYS         — глубина новостей в днях (7)
"""

from __future__ import annotations

import email.utils
import gzip
import hashlib
import html as _html
import json
import math
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
TIERS = {t.strip().lower() for t in os.environ.get("TIERS", "s,a").split(",") if t.strip()}  # тир-1 (s) и тир-2 (a)
STATS = os.environ.get("STATS_ONLY") == "1"   # быстрый сбор статистики: без HLTV, Liquipedia и нейросети
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "21"))  # расписание: BLAST и др. публикуют заранее
DETAIL_HOURS = int(os.environ.get("DETAIL_HOURS", "72"))
NEWS_DAYS = int(os.environ.get("NEWS_DAYS", "7"))
MAPS_WINDOW_DAYS = 90  # статистика карт — за 3 месяца, как и остальная

# Ленты профильных сайтов. Любую можно убрать или добавить свою.
# Новостные Telegram-каналы про CS (лента на главном экране + новости в карточках матчей).
TG_NEWS_CHANNELS = ["cs3news", "retakenews", "raiznews", "newcsgo", "m0NESYGG"]
TG_PAGES = 20                # до ~400 постов на канал: листаем назад, пока не дойдём до постов старше NEWS_DAYS

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


NAME_RU = {  # русские имена, которые владелец поправил вручную (важнее нейросети): ник → «Имя Фамилия»
    "pr": "Олдрич Новый",
    "xertion": "Дориан Берман",
    "sycrone": "Деннис Нильсен",
    "xelex": "Адриан Винце",
}


def apply_name_ru(x) -> None:
    """Ручные русские имена — везде, где в данных встречается человек с ником (игроки, тренеры, статистика)."""
    if isinstance(x, dict):
        k = norm(x.get("nick")) if isinstance(x.get("nick"), str) else ""
        if k in NAME_RU and ("name_ru" in x or "first_name" in x or "name" in x or "role" in x):
            x["name_ru"] = NAME_RU[k]
        for v in x.values():
            if isinstance(v, (dict, list)):
                apply_name_ru(v)
    elif isinstance(x, list):
        for v in x:
            apply_name_ru(v)


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name in ("match", "team") or path.name == "matches.json":
        apply_name_ru(data)
    if pretty:
        txt = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    else:
        txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)  # что-то непривычное — текстом, а не падение сбора
    path.write_text(txt, "utf-8")


GAMES_CACHE_F = CACHE / "bo3_games_v2.json"   # карты по завершённым матчам bo3, как отдаёт bo3 (не меняются)
DETAILS_CACHE_F = CACHE / "bo3_game_details_v2.json"  # раунды и статистика игроков по каждой карте
TEAMS_CACHE_F = CACHE / "bo3_teams.json"     # PandaScore id → bo3 id
TR_CACHE_F = CACHE / "translations.json"     # переводы заголовков
ROSTER_CACHE_F = CACHE / "liquipedia_rosters_v3.json"  # тренер, роли, даты рождения с Liquipedia
PRON_CACHE_F = CACHE / "pronunciation_v2.json"       # имена и ники по-русски с ударением
games_cache: dict = load_json(GAMES_CACHE_F, {})
details_cache: dict = load_json(DETAILS_CACHE_F, {})
DETAILS_NEW_LIMIT = int(os.environ.get("DETAILS_NEW_LIMIT", "1500" if os.environ.get("STATS_ONLY") == "1" else "3500"))  # bo3 — открытый API; 450 не хватало, мелкие команды оставались без статистики игроков  # новых карт с раундами за запуск
_details_new = [0]
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


# ---------------------------------------------------------------- тир-2: только турниры, где не меньше половины команд — уровня тир-1
TIER1_TOP = int(os.environ.get("TIER1_TOP", "25"))     # «уровень тир-1» = топ-25 рейтинга Valve
TIER2_SHARE = float(os.environ.get("TIER2_SHARE", "0.5"))
_top_teams: list = []


def top_teams() -> list[str]:
    if not _top_teams:
        rk = ((load_json(OUT / "ranking.json", {}) or {}).get("vrs") or {}).get("teams") or []
        _top_teams.extend(norm(t.get("name")) for t in rk if (t.get("rank") or 999) <= TIER1_TOP and t.get("name"))
    return _top_teams


def is_top(name: str | None) -> bool:
    n = norm(name)
    return bool(n) and any(n == k or (min(len(n), len(k)) >= 3 and (n in k or k in n)) for k in top_teams())


def tier2_ok(teams) -> bool:
    """Турнир тир-2 показываем, если хотя бы половина участников — из топ-25 рейтинга Valve."""
    names = {norm(t) for t in teams or [] if norm(t)}
    if not top_teams():
        return True   # рейтинга ещё нет — ничего не прячем
    if not names:
        return False  # участники неизвестны — подождём, пока объявят
    return sum(1 for n in names if is_top(n)) / len(names) >= TIER2_SHARE


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


TOURS_MONTHS_AHEAD = 3


_future_cache: list = []


def fetch_future_tournaments() -> list[dict]:
    """Идущие и будущие турниры тир-1 на TOURS_MONTHS_AHEAD месяцев вперёд, сгруппированные по серии (событию)."""
    if not _future_cache:
        _future_cache.append(_fetch_future_tournaments())
    return [dict(x) for x in _future_cache[0]]


def _fetch_future_tournaments() -> list[dict]:
    end = NOW + timedelta(days=31 * TOURS_MONTHS_AHEAD)
    stages: list[dict] = []
    for path in ("/csgo/tournaments/running", "/csgo/tournaments/upcoming"):
        for page in (1, 2, 3):
            params = {"per_page": 100, "page": page, "sort": "begin_at"}
            if path.endswith("upcoming"):
                params["range[begin_at]"] = f"{iso(NOW - timedelta(days=1))},{iso(end)}"
            try:
                batch = panda(path, params)
            except Exception as e:
                warn(f"Турниры {path}: {e!r}")
                break
            stages += [t for t in batch if (t.get("tier") or "").lower() in TIERS]
            if len(batch) < 100:
                break
    series: dict = {}
    for t in stages:
        se = t.get("serie") or {}
        lg = t.get("league") or {}
        sid = str(se.get("id") or t.get("serie_id") or t["id"])
        name = " ".join(x for x in (lg.get("name"), se.get("full_name") or se.get("name")) if x).strip()
        cur = series.setdefault(sid, {"id": sid, "name": name, "tier": (t.get("tier") or "").lower(), "image": lg.get("image_url"),
                                      "begin": se.get("begin_at") or t.get("begin_at"), "end": se.get("end_at") or t.get("end_at"),
                                      "prize": t.get("prizepool"), "matches": 0, "next": None, "live": 0, "teams": []})
        cur["teams"] += [x.get("name") for x in t.get("teams") or [] if x.get("name") and x.get("name") not in cur["teams"]]
        for k, f in (("begin", min), ("end", max)):
            vals = [x for x in (cur.get(k), t.get("begin_at" if k == "begin" else "end_at")) if x]
            cur[k] = f(vals) if vals else None
        if (t.get("tier") or "").lower() == "s":
            cur["tier"] = "s"
        cur["prize"] = cur.get("prize") or t.get("prizepool")
    return list(series.values())


def tour_norm(name: str) -> str:
    n = (name or "").lower().replace("intel extreme masters", "iem").replace("esl pro league", "epl")
    return " ".join(re.findall(r"[a-z0-9]+", n))


def tour_same(a: str, b_norm: str) -> bool:
    ta, tb = set(tour_norm(a).split()), set(b_norm.split())
    return bool(ta) and len(ta & tb) / len(ta | tb) >= 0.6


def bo3_tour_teams(t: dict) -> list[str]:
    """Участники турнира bo3.gg (из карточки турнира)."""
    try:
        d = bo3(f"/tournaments/{t['slug']}")
    except Exception as e:
        warn(f"bo3 участники {t.get('slug')}: {e!r}")
        return []
    out = []
    for x in d.get("teams") or []:
        x = x.get("team") if isinstance(x.get("team"), dict) else x
        if x.get("name"):
            out.append(x["name"])
    return out


def fetch_bo3_tournaments() -> list[dict]:
    """Ближайшие турниры тир-1 с bo3.gg — подстраховка, если PandaScore ещё не завёл турнир (например, мейджор)."""
    end = iso(NOW + timedelta(days=31 * TOURS_MONTHS_AHEAD))
    out = []
    try:
        j = bo3("/tournaments", {"page[limit]": 30, "sort": "start_date", "filter[tournaments.tier][in]": ",".join(sorted(TIERS)),
                                 "filter[tournaments.status][in]": "upcoming,current"})
    except Exception as e:
        warn(f"bo3 турниры: {e!r}")
        return out
    for t in j.get("results") or []:
        if (t.get("tier") or "").lower() not in TIERS or (t.get("start_date") or "") > end:
            continue
        if (t.get("tier") or "").lower() != "s" and not tier2_ok(bo3_tour_teams(t)):
            continue
        name = (t.get("name") or "").replace("Intel Extreme Masters", "IEM")
        out.append({"id": "bo3-" + str(t["id"]), "name": name, "tier": (t.get("tier") or "").lower(), "image": t.get("image_url"),
                    "begin": (parse_iso(t.get("start_date")) and iso(parse_iso(t["start_date"]))),
                    "end": (parse_iso(t.get("end_date")) and iso(parse_iso(t["end_date"]))),
                    "prize": None, "prize_usd": t.get("prize"), "matches": 0, "next": None, "live": 0})
    return out


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


_lp_blocked = [False]
# старые сохранённые составы — запасной вариант, если Liquipedia не отвечает
_roster_fallback: dict = {}
for _f in ("liquipedia_rosters_v2.json", "liquipedia_rosters.json"):
    for _k, _v in load_json(CACHE / _f, {}).items():
        if _v.get("data") and _k not in _roster_fallback:
            _roster_fallback[_k] = _v


def lp_pages(titles: list[str]) -> dict[str, str]:
    """Вики-текст страниц Liquipedia: {запрошенное название: текст}. Не чаще запроса в 2,5 с (правила Liquipedia)."""
    if _lp_blocked[0]:
        raise RuntimeError("Liquipedia ограничила запросы в этом запуске")
    url = LP_API + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "titles": "|".join(titles), "redirects": 1, "format": "json"})
    for attempt in (0, 1):
        wait = 4.0 - (time.time() - _lp_last[0])  # бережнее правил Liquipedia (2 с) — составов за сбор стало больше
        if wait > 0:
            time.sleep(wait)
        _lp_last[0] = time.time()
        stats["liquipedia_requests"] += 1
        try:
            data = json.loads(http_get(url, {"User-Agent": LP_UA, "Accept": "application/json"}))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                warn("Liquipedia: «слишком часто» — жду 90 с и пробую ещё раз")
                time.sleep(90)
                continue
            if e.code == 429:
                _lp_blocked[0] = True  # повторно — больше не стучимся до следующего запуска
            raise
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
    if _lp_blocked[0]:
        return None
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


def infobox(wikitext: str) -> str:
    """Текст инфобокса игрока: от «{{Infobox player» до его закрывающих скобок (с учётом вложенных шаблонов)."""
    i = wikitext.find("{{Infobox")
    if i < 0:
        return wikitext[:6000]
    depth, j = 0, i
    while j < len(wikitext) - 1:
        two = wikitext[j:j + 2]
        if two == "{{":
            depth += 1
            j += 2
            continue
        if two == "}}":
            depth -= 1
            j += 2
            if depth == 0:
                return wikitext[i:j]
            continue
        j += 1
    return wikitext[i:i + 8000]


def infobox_team(wikitext: str) -> str | None:
    """Команда игрока по инфобоксу (|team=…) — для аренды это клуб, которому принадлежит контракт."""
    m = re.search(r"\|\s*team\s*=\s*([^\n|}]*)", infobox(wikitext))
    v = m.group(1).strip() if m else ""
    return v or None


def player_roles(wikitext: str) -> tuple[list[str], bool]:
    """Текущие роли из инфобокса игрока (roles=igl,entry / role=…). Возвращает (роли, igl)."""
    box = infobox(wikitext)
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
        res.append("Рифл + AWPer")
    elif awp:
        res.append("AWP")
    elif rifle:
        res.append("Рифлер" + (f" · {rifle_kind}" if rifle_kind else ""))
    return res, igl


def birth_date(wikitext: str) -> str | None:
    """Дата рождения из инфобокса: |birth_date={{Birth date and age|2005|5|1}} или |birth_date=2005-05-01."""
    m = re.search(r"\|\s*birth_date\s*=\s*([^\n]*)", infobox(wikitext))
    if not m:
        return None
    v = m.group(1)
    d = re.search(r"(\d{4})\s*[|-]\s*(\d{1,2})\s*[|-]\s*(\d{1,2})", v)
    if d:
        return f"{int(d.group(1)):04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
    y = re.search(r"(19|20)\d{2}", v)
    return y.group(0) if y else None


def age_from(birth: str | None) -> int | None:
    if not birth:
        return None
    try:
        if len(birth) == 4:
            return NOW.year - int(birth)
        b = datetime.fromisoformat(birth).replace(tzinfo=timezone.utc)
        return NOW.year - b.year - ((NOW.month, NOW.day) < (b.month, b.day))
    except Exception:
        return None


def lp_roster(t: dict) -> dict | None:
    """Активный состав с Liquipedia: тренер, капитан, роли. Кэш на ROSTER_TTL_DAYS дней."""
    key = str(t["id"])
    cached = roster_cache.get(key)
    if cached:
        age = NOW - (parse_iso(cached.get("t")) or NOW)
        d = cached.get("data") or {}
        incomplete = not d or any(not p.get("roles") for p in d.get("players") or []) or not d.get("coach")
        # полный состав живёт ROSTER_TTL_DAYS; неполный (нет ролей или тренера) перепроверяем каждые 3 часа
        if age < timedelta(days=ROSTER_TTL_DAYS) and not (incomplete and age >= timedelta(hours=3)):
            return cached.get("data")
    names = []
    base = re.sub(r"\s+(esports|e-sports|gaming|team|clan|club)$", "", t.get("name") or "", flags=re.IGNORECASE).strip()
    for nm in (t.get("name"), re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.get("name") or ""), t.get("acronym"),
               "Team " + base if base else None, base + " Esports" if base else None):  # на Liquipedia: «Team Spirit», «FUT Esports»
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
            # активный состав бывает несколькими блоками: игроки и отдельно штаб (type=staff — тренеры); запасные (inactive) не берём
            blocks, i = [], text.find("Squad|status=active")
            while i >= 0:
                blocks.append(text[i: text.find("\n}}", i)])
                i = text.find("Squad|status=active", i + 10)
            block = "\n".join(blocks)
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
                    "loan_from": lp_param(line, "team"),
                })
            players = [p for p in people if not p["coach"]]
            ppages = lp_pages([p["page"] for p in people]) if people else {}
            for p in people:
                p["birth"] = birth_date(ppages.get(p["page"], ""))
                if p.get("note") and "loan" in p["note"]:
                    owner = p.get("loan_from") or infobox_team(ppages.get(p["page"], ""))
                    if owner and norm(owner) not in (norm(t.get("name")), norm(t.get("acronym"))):
                        p["loan_from"] = owner
                    else:
                        p["loan_from"] = None
            any_igl = any(p["igl"] for p in players)
            for p in players:
                roles, igl = player_roles(ppages.get(p["page"], ""))
                p["roles"] = roles
                if not any_igl and igl:
                    p["igl"] = True  # в составе капитан не отмечен — берём со страницы игрока
            data = {
                "coach": [{"nick": p["nick"], "name": p["name"], "flag": p["flag"], "birth": p["birth"]} for p in people if p["coach"]],
                "players": [{k: p.get(k) for k in ("nick", "name", "flag", "igl", "roles", "note", "birth", "loan_from")} for p in players],
            }
    except Exception as e:
        warn(f"Liquipedia {t.get('name')}: {e!r}")
        if cached:
            return cached.get("data")
        fb = _roster_fallback.get(key)
        return fb.get("data") if fb else None
    roster_cache[key] = {"t": iso(NOW), "data": data}
    return data


NAME_FIX = {"im": ("Mihai", "Ivan", "Михай Иван")}   # порядок имени и фамилии, где источники путают


def fix_names(people: list[dict]) -> None:
    for p in people:
        f = NAME_FIX.get(norm(p.get("nick")))
        if f:
            p["first_name"], p["last_name"], p["name_ru"] = f[0], f[1], f[2]
            p.pop("_pk", None)


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
            note = lp_p["note"]
            if "loan" in note:
                tags.append("В аренде из " + lp_p["loan_from"] if lp_p.get("loan_from") else "В аренде")
            else:
                tags.append({"stand-in": "Замена", "inactive": "Неактивен"}.get(note, note))
        first, last = pp.get("first_name"), pp.get("last_name")
        if not (first or last) and lp_p.get("name"):
            first, last = lp_p["name"], None
        res.append({
            "id": pp.get("id"),
            "nick": lp_p["nick"],
            "first_name": first,
            "last_name": last,
            "nationality": pp.get("nationality") or lp_p.get("flag"),
            "age": age_from(lp_p.get("birth")) or pp.get("age"),
            "birth": lp_p.get("birth"),
            "image": pp.get("image"),
            "tags": tags,
        })
    coach = [{**c, "age": age_from(c.get("birth"))} for c in lp.get("coach") or []]
    return res, coach

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
        bare = re.sub(r"\s+(esports|e-sports|gaming|team|clan|club)$", "", t["name"], flags=re.IGNORECASE).strip()  # FUT Esports → FUT
        for nm in dict.fromkeys([t["name"], spaced, bare]):
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
            add_alias(found, pick.get("name"), pick.get("slug"), pick.get("acronym"))
            break
    if found is None:
        warn(f"bo3: не нашёл команду {t.get('name')}")
    else:
        teams_map[key] = found
    return found


_bo3_hist: dict[int, list] = {}


def bo3_upcoming(bid: int, limit: int = 10) -> list[dict]:
    """Ближайшие матчи команды (bo3.gg): дата, соперник, турнир, формат — для страницы команды."""
    try:
        j = bo3("/matches", {"page[limit]": limit, "sort": "start_date", "filter[matches.status][in]": "upcoming,current",
                             "filter[matches.team_ids][overlap]": bid, "with": "teams,tournament"})
    except Exception as e:
        warn(f"bo3 ближайшие {bid}: {e}")
        return []
    out = []
    for m in j.get("results") or []:
        me1 = m.get("team1_id") == bid
        opp = (m.get("team2") if me1 else m.get("team1")) or {}
        out.append({"sid": str(m.get("id")), "date": m.get("start_date"), "opponent_name": opp.get("name") or "TBD", "logo": opp.get("image_url"),
                    "tournament": tour_name(m), "bo": m.get("bo_type"), "live": m.get("status") == "current"})
    return out


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


GAMES_NEW_LIMIT = int(os.environ.get("GAMES_NEW_LIMIT", "2000"))  # bo3 — открытый API
_games_new = [0]


def bo3_games(m: dict) -> list[dict]:
    """Карты матча как есть: [{'map','n','wn','ln','ws','ls'}] (кто выиграл — по клан-тегу). Кэш навсегда."""
    key = str(m["id"])
    if key in games_cache:
        return games_cache[key]
    if _games_new[0] >= GAMES_NEW_LIMIT:
        return []  # лимит новых запросов за запуск — догрузим в следующий раз
    _games_new[0] += 1
    try:
        games = bo3("/games", {"filter[games.match_id][eq]": m["id"]}).get("results") or []
    except Exception as e:
        warn(f"bo3 карты {m['id']}: {e}")
        return []
    res = []
    for g in sorted(games, key=lambda g: g.get("number") or 0):
        if g.get("status") != "finished":
            continue
        res.append({"id": g.get("id"), "map": map_title(g.get("map_name")), "n": g.get("number"),
                    "wn": g.get("winner_clan_name"), "ln": g.get("loser_clan_name"),
                    "ws": g.get("winner_clan_score"), "ls": g.get("loser_clan_score")})
    if m.get("status") == "finished":
        games_cache[key] = res
    return res


def game_detail(gid: int | None) -> dict | None:
    """Раунды (кто выиграл, за какую сторону) и статистика игроков на карте. Кэш навсегда."""
    if not gid:
        return None
    key = str(gid)
    if key in details_cache:
        return details_cache[key]
    if _details_new[0] >= DETAILS_NEW_LIMIT:
        return None
    _details_new[0] += 1
    try:
        g = bo3(f"/games/{gid}")  # здесь и раунды, и какой клан какой команде принадлежит
        ps = bo3(f"/games/{gid}/players_stats")
    except Exception as e:
        warn(f"bo3 карта {gid}: {e!r}")
        return None
    wtc, ltc = g.get("winner_team_clan") or {}, g.get("loser_team_clan") or {}
    clan2team = {wtc.get("clan_name"): wtc.get("team_id"), ltc.get("clan_name"): ltc.get("team_id")}
    rounds = []
    for r in sorted(g.get("game_rounds") or [], key=lambda r: r.get("round_number") or 0):
        tid = clan2team.get(r.get("winner_clan_name"))
        rounds.append([r.get("round_number"), tid, (r.get("winner_clan_side") or "")[:2]])
    players = []
    for x in ps if isinstance(ps, list) else []:
        sp = x.get("steam_profile") or {}
        players.append({
            "tid": (x.get("team_clan") or {}).get("team_id") or clan2team.get(x.get("clan_name")),
            "nick": sp.get("nickname"), "pid": sp.get("player_id"),
            "k": x.get("kills"), "d": x.get("death"), "a": x.get("assists"), "adr": x.get("adr"),
            "kast": x.get("kast"), "r": x.get("player_rating"), "fk": x.get("first_kills"), "fd": x.get("first_death"),
            "cl": x.get("clutches"), "hs": x.get("headshots"),
        })
    d = {"wt": wtc.get("team_id"), "lt": ltc.get("team_id"), "rounds": rounds, "players": players}
    if g.get("status") == "finished":
        details_cache[key] = d
    return d


# Все известные названия команды: официальное, аббревиатура, слаг — с PandaScore и bo3.
team_aliases: dict[int, set] = {}


def add_alias(bid: int | None, *names: str | None) -> None:
    if not bid:
        return
    st = team_aliases.setdefault(bid, set())
    for n in names:
        if n and norm(n):
            st.add(norm(n))
            # «The MongolZ» → «mongolz», «Team Falcons» → «falcons»
            st.add(norm(re.sub(r"^(the|team)\s+|\s+(team|esports|gaming|clan)$", "", n, flags=re.IGNORECASE)))
    st.discard("")


def clan_is(clan: str | None, names: set) -> bool:
    c = norm(clan)
    if not c or not names:
        return False
    if c in names:
        return True
    return any(len(n) >= 3 and len(c) >= 3 and (c in n or n in c) for n in names)


def map_results(m: dict, bid: int) -> list[dict]:
    """Карты матча с точки зрения команды bid: [{'map','won': True/False/None,'score'}].
    Победитель карты определяется по клан-тегу и сверяется со счётом серии; если не сходится — None."""
    games = bo3_games(m)
    if not games:
        return []
    s = side_of(m, bid)
    ours = set(team_aliases.get(bid, set()))
    me_obj = (m.get("team1") if s == 1 else m.get("team2")) or {}
    opp_obj = opp_of(m, bid)
    add_alias(bid, me_obj.get("name"), me_obj.get("slug"))
    ours |= team_aliases.get(bid, set())
    theirs = {norm(opp_obj.get("name")), norm(opp_obj.get("slug")), norm(opp_obj.get("acronym"))} - {""}
    theirs -= ours
    out = []
    for g in games:
        det = details_cache.get(str(g.get("id"))) if g.get("id") else None
        if det and det.get("wt"):
            won = det["wt"] == bid
            out.append({"map": g["map"], "won": won, "ws": g["ws"], "ls": g["ls"]})
            continue
        votes = set()
        if clan_is(g["wn"], ours): votes.add(True)
        if clan_is(g["ln"], ours): votes.add(False)
        if clan_is(g["wn"], theirs): votes.add(False)
        if clan_is(g["ln"], theirs): votes.add(True)
        won = votes.pop() if len(votes) == 1 else None
        out.append({"map": g["map"], "won": won, "ws": g["ws"], "ls": g["ls"]})
    my = m.get("team1_score") if s == 1 else m.get("team2_score")
    their = m.get("team2_score") if s == 1 else m.get("team1_score")
    if isinstance(my, int) and isinstance(their, int):
        kw = sum(1 for r in out if r["won"] is True)
        kl = sum(1 for r in out if r["won"] is False)
        if kw > my or kl > their:
            for r in out:
                r["won"] = None  # клан-теги не сходятся со счётом серии — не угадываем
        elif len(out) == my + their:
            unknown = [r for r in out if r["won"] is None]
            if unknown and my - kw == 0:
                for r in unknown: r["won"] = False
            elif unknown and their - kl == 0:
                for r in unknown: r["won"] = True
    for r in out:
        ws, ls = r.pop("ws"), r.pop("ls")
        if r["won"] is None or ws is None or ls is None:
            r["score"] = None
        else:
            r["score"] = f"{ws}:{ls}" if r["won"] else f"{ls}:{ws}"
    return out


def side_of(m: dict, bid: int) -> int:
    return 1 if m.get("team1_id") == bid else 2


def opp_of(m: dict, bid: int) -> dict:
    return (m.get("team2") if m.get("team1_id") == bid else m.get("team1")) or {}


def tour_name(m: dict) -> str:
    return ((m.get("tournament") or {}).get("name") or "").strip()


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
            "opponent_name": opp.get("name"), "logo": opp.get("image_url"),
            "score": my, "opp_score": their,
            "won": m.get("winner_team_id") == bid,
            "tournament": tour_name(m),
            "tier": (m.get("tier") or "").lower(),
            "sid": m.get("id"),
        })
    return res


H2H_DAYS = 365


def bo3_h2h_matches(a: int, b: int) -> list[dict]:
    """Все завершённые матчи между двумя командами — точным запросом (обе расстановки team1/team2)."""
    res = {}
    for t1, t2 in ((a, b), (b, a)):
        try:
            j = bo3("/matches", {"page[limit]": 50, "sort": "-start_date",
                                 "filter[matches.status][in]": "finished",
                                 "filter[matches.team1_id][eq]": t1, "filter[matches.team2_id][eq]": t2,
                                 "with": "teams,tournament"})
            for m in j.get("results") or []:
                if {m.get("team1_id"), m.get("team2_id")} == {a, b}:
                    res[m["id"]] = m
        except Exception as e:
            warn(f"bo3 личные встречи {a}/{b}: {e!r}")
    return sorted(res.values(), key=lambda m: m.get("start_date") or "", reverse=True)


_h2h_matches: dict = {}


def build_h2h(a: int, b: int, hist_a: list[dict]) -> list[dict]:
    since = datetime(NOW.year, 1, 1, tzinfo=timezone.utc)  # «за год» — текущий календарный год
    exact = bo3_h2h_matches(a, b)
    if exact:
        hist_a = exact
    res = []
    for m in hist_a:
        if {m.get("team1_id"), m.get("team2_id")} != {a, b}:
            continue
        d = parse_iso(m.get("start_date"))
        if d and d < since:
            break
        sa = side_of(m, a)
        games = bo3_games(m)
        maps = []
        for i, r in enumerate(map_results(m, a)):
            win = None if r["won"] is None else (0 if r["won"] else 1)
            sides = None
            if i < len(games) and games[i].get("id") and win is not None:  # раунды за CT/T — для квадратов карт, как в сыгранных сериях
                det = details_cache.get(str(games[i]["id"])) or game_detail(games[i]["id"])
                sides = map_sides(det, win)
            maps.append({"map": r["map"], "winner": win, "score": r["score"], "sides": sides})
        _h2h_matches[str(m.get("id"))] = m
        res.append({
            "sid": str(m.get("id")),  # по нажатию открывается серия со статистикой и вето
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
            opp = opp_of(m, bid)
            for r in map_results(m, bid):
                name = r.get("map")
                if not name or r["won"] is None:
                    continue
                row = table.setdefault(name, {"map": name, "teams": [{"played": 0, "won": 0}, {"played": 0, "won": 0}]})
                row["teams"][idx]["played"] += 1
                row["teams"][idx]["won"] += int(r["won"])
                recent.setdefault(name, []).append({
                    "team": idx, "date": m.get("start_date"), "vs": opp.get("acronym") or opp.get("name"), "logo": opp.get("image_url"),
                    "won": r["won"], "score": r["score"], "tournament": tour_name(m), "sid": m.get("id")})
    for name in recent:
        recent[name].sort(key=lambda r: r["date"] or "", reverse=True)
    rows = sorted(table.values(), key=lambda r: -(r["teams"][0]["played"] + r["teams"][1]["played"]))
    return {"available": bool(rows), "window_days": MAPS_WINDOW_DAYS, "table": rows, "recent": recent}


STATS_WINDOW_DAYS = 90


def team_games(bid: int, hist: list[dict], max_matches: int = 30):
    """Карты команды за STATS_WINDOW_DAYS: (матч, карта, детали)."""
    since = NOW - timedelta(days=STATS_WINDOW_DAYS)
    for m in hist[:max_matches]:
        d = parse_iso(m.get("start_date"))
        if d and d < since:
            break
        for g in bo3_games(m):
            det = game_detail(g.get("id"))
            if det:
                yield m, g, det


def build_sides(bids: list[int | None], hists: list[list[dict]]) -> dict:
    """По каждой карте и в целом: % выигранных раундов за CT и T, % пистолеток (раунды 1 и 13)."""
    res: dict = {"window_days": STATS_WINDOW_DAYS, "maps": {}, "total": [None, None]}
    for idx, (bid, hist) in enumerate(zip(bids, hists)):
        if not bid:
            continue
        agg: dict = {}
        for m, g, det in team_games(bid, hist):
            if not det.get("rounds"):
                continue
            for key in (g.get("map") or "?", "__all"):
                a = agg.setdefault(key, {"ct": [0, 0], "t": [0, 0], "pistol": [0, 0], "maps": 0})
                a["maps"] += 1
                for n, tid, side in det["rounds"]:
                    if not side or tid is None:
                        continue
                    ours = tid == bid
                    my_side = side if ours else ("T" if side == "CT" else "CT")
                    k = "ct" if my_side == "CT" else "t"
                    a[k][1] += 1
                    a[k][0] += int(ours)
                    if n in (1, 13):
                        a["pistol"][1] += 1
                        a["pistol"][0] += int(ours)
        for key, a in agg.items():
            pct = {k: (round(100 * a[k][0] / a[k][1]) if a[k][1] else None) for k in ("ct", "t", "pistol")}
            row = {**pct, "maps": a["maps"], "pistol_n": a["pistol"]}
            if key == "__all":
                res["total"][idx] = row
            else:
                res["maps"].setdefault(key, [None, None])[idx] = row
    return res


BO3_PLAYERS_F = CACHE / "bo3_players.json"
bo3_players: dict = load_json(BO3_PLAYERS_F, {})   # id игрока bo3 → основной ник


def bo3_player_nick(pid) -> str | None:
    if not pid:
        return None
    k = str(pid)
    if k in bo3_players:
        return bo3_players[k]
    try:
        res = bo3("/players", {"filter[players.id][eq]": pid}).get("results") or []
        nick = next((r.get("nickname") for r in res if str(r.get("id")) == k), None)
    except Exception as e:
        warn(f"bo3 игрок {pid}: {e!r}")
        return None
    bo3_players[k] = nick
    return nick


_bo3_team_players: dict = {}


def bo3_team_players(bid: int, hist: list[dict]) -> dict:
    """id игрока bo3 → ник по составу команды на bo3.gg (запрос команды вместе с игроками)."""
    if bid in _bo3_team_players:
        return _bo3_team_players[bid]
    slug = None
    for m in hist[:10]:
        for side in ("team1", "team2"):
            t = m.get(side) or {}
            if t.get("id") == bid and t.get("slug"):
                slug = t["slug"]
                break
        if slug:
            break
    res = {}
    if slug:
        try:
            for t in bo3("/teams", {"filter[teams.slug][eq]": slug, "with": "players"}).get("results") or []:
                if t.get("id") == bid:
                    for p in t.get("players") or []:
                        if p.get("id") and p.get("nickname"):
                            res[str(p["id"])] = p["nickname"]
        except Exception as e:
            warn(f"bo3 состав {slug}: {e!r}")
    _bo3_team_players[bid] = res
    return res


def match_roster(x: dict, roster: dict, team_ids: dict | None = None) -> str | None:
    """Ключ игрока из состава для строки статистики: по нику в Steam, по частичному совпадению, по id игрока на bo3."""
    k = norm(x.get("nick"))
    if k in roster:
        return k
    for r in roster:
        if len(r) >= 3 and (r in k or (len(k) >= 3 and k in r)):
            return r
    canon = norm((team_ids or {}).get(str(x.get("pid"))) or "")
    if canon in roster:
        return canon
    import difflib
    best = max(roster, key=lambda r: difflib.SequenceMatcher(None, r, k).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, best, k).ratio() >= 0.8:
        return best  # опечатки в нике Steam: Martineez → Martinez
    return None


def build_player_stats(team: dict, bid: int | None, hist: list[dict]) -> list[dict]:
    """Статистика игроков состава за STATS_WINDOW_DAYS по картам bo3.gg."""
    if not bid:
        return []
    roster = {norm(p.get("nick")): p.get("nick") for p in team.get("players") or []}
    team_ids = bo3_team_players(bid, hist)
    agg: dict = {}
    for m, g, det in team_games(bid, hist):
        for x in det.get("players") or []:
            if x.get("tid") != bid or not x.get("nick"):
                continue
            key = match_roster(x, roster, team_ids) if roster else norm(x["nick"])
            if not key:
                continue
            a = agg.setdefault(key, {"nick": roster.get(key, x["nick"]), "maps": 0, "k": 0, "d": 0, "adr": 0.0, "kast": 0.0,
                                     "r": 0.0, "fk": 0, "fd": 0, "cl": 0, "per_map": {}})
            a["maps"] += 1
            a["k"] += x.get("k") or 0
            a["d"] += x.get("d") or 0
            a["adr"] += x.get("adr") or 0
            a["kast"] += x.get("kast") or 0
            a["r"] += x.get("r") or 0
            a["fk"] += x.get("fk") or 0
            a["fd"] += x.get("fd") or 0
            a["cl"] += x.get("cl") or 0
            pm = a["per_map"].setdefault(g.get("map") or "?", [0, 0.0])
            pm[0] += 1
            pm[1] += x.get("r") or 0
    out = []
    for a in agg.values():
        n = a["maps"] or 1
        best = sorted(((mp, v[1] / v[0], v[0]) for mp, v in a["per_map"].items() if v[0] >= 3), key=lambda t: -t[1])
        out.append({
            "nick": a["nick"], "maps": a["maps"],
            "rating": round(a["r"] / n, 2), "kd": round(a["k"] / a["d"], 2) if a["d"] else None,
            "adr": round(a["adr"] / n, 1), "kast": round(100 * a["kast"] / n), "opening": a["fk"] - a["fd"],
            "clutches": a["cl"], "best_map": best[0][0] if best else None, "worst_map": best[-1][0] if len(best) > 1 else None,
        })
    for x in out:
        hs = hltv_player_stats(x["nick"])
        x["hltv"] = hs
    # игроки состава без карт на bo3, но со статистикой HLTV
    have = {norm(x["nick"]) for x in out}
    for p in team.get("players") or []:
        if norm(p.get("nick")) not in have:
            hs = hltv_player_stats(p.get("nick") or "")
            if hs:
                out.append({"nick": p["nick"], "hltv": hs, "best_map": None})
    out.sort(key=lambda x: -((x.get("hltv") or {}).get("rating") or 0))
    return out



# ---------------------------------------------------------------- подробности серии: карты, счёт, статистика игроков

_nick_new = [0]


def series_players(rows: list[dict], tid, names: dict) -> list[list]:
    """Строки статистики одной команды: [ник, убийства, смерти, ADR, KAST %, рейтинг] — по рейтингу."""
    agg: dict = {}
    for x in rows:
        if x.get("tid") != tid:
            continue
        pid = str(x.get("pid") or "")
        nick = names.get(pid) or bo3_players.get(pid) or x.get("nick") or "?"
        if pid and pid not in names and pid not in bo3_players and _nick_new[0] < 120:
            _nick_new[0] += 1
            nick = bo3_player_nick(pid) or nick
        a = agg.setdefault(pid or nick, {"nick": nick, "n": 0, "k": 0, "d": 0, "adr": 0.0, "kast": 0.0, "r": 0.0})
        a["n"] += 1
        a["k"] += x.get("k") or 0
        a["d"] += x.get("d") or 0
        a["adr"] += x.get("adr") or 0
        a["kast"] += x.get("kast") or 0
        a["r"] += x.get("r") or 0
    out = []
    for a in agg.values():
        n = a["n"] or 1
        out.append([a["nick"], a["k"], a["d"], round(a["adr"] / n, 1), round(100 * a["kast"] / n), round(a["r"] / n, 2)])
    out.sort(key=lambda r: -r[5])
    return out


def map_sides(det: dict | None, win: int | None) -> list | None:
    """Раунды по сторонам на карте: [[CT, T] команды 1, [CT, T] команды 2]. win — индекс победителя карты (0/1)."""
    if not det or win not in (0, 1) or not det.get("rounds"):
        return None
    out = [[0, 0], [0, 0]]
    for _, tid, side in det["rounds"]:
        if tid is None or not side:
            continue
        i = win if tid == det.get("wt") else 1 - win
        out[i][0 if side.upper().startswith("CT") else 1] += 1
    return out if sum(map(sum, out)) else None


def series_detail(m: dict) -> dict | None:
    """Завершённая серия bo3: карты со счётом и статистика игроков (по серии и по каждой карте)."""
    t1, t2 = m.get("team1_id"), m.get("team2_id")
    if not t1 or not t2:
        return None
    games = bo3_games(m)
    names = {}
    names.update(_bo3_team_players.get(t1) or {})
    names.update(_bo3_team_players.get(t2) or {})
    maps, every = [], []
    for g in games:
        det = game_detail(g.get("id")) or {}
        win = 0 if det.get("wt") == t1 else 1 if det.get("wt") == t2 else None
        ws, ls = g.get("ws"), g.get("ls")
        score = ([ws, ls] if win == 0 else [ls, ws]) if win is not None and ws is not None and ls is not None else None
        rows = det.get("players") or []
        every += rows
        maps.append({"map": g.get("map"), "score": score, "winner": win, "sides": map_sides(det, win),
                     "players": [series_players(rows, t1, names), series_players(rows, t2, names)] if rows else None})
    tm1, tm2 = m.get("team1") or {}, m.get("team2") or {}
    return {
        "id": m["id"], "date": m.get("start_date"), "bo": m.get("bo_type"),
        "tournament": tour_name(m) or None,
        "teams": [{"name": tm1.get("name"), "acronym": tm1.get("acronym"), "score": m.get("team1_score"), "logo": tm1.get("image_url")},
                  {"name": tm2.get("name"), "acronym": tm2.get("acronym"), "score": m.get("team2_score"), "logo": tm2.get("image_url")}],
        "winner": 0 if m.get("winner_team_id") == t1 else 1 if m.get("winner_team_id") == t2 else None,
        "maps": maps,
        "players": [series_players(every, t1, names), series_players(every, t2, names)] if every else None,
    }


ROUND_RU = [(r"grand final", "гранд-финал"), (r"upper bracket", "верхняя сетка"), (r"lower bracket", "нижняя сетка"),
            (r"quarter-?finals?", "четвертьфинал"), (r"semi-?finals?", "полуфинал"), (r"\bfinals?\b", "финал"),
            (r"\bround\b", "раунд"), (r"\bgroup\b", "группа"), (r"opening( matches?)?", "стартовые матчи"),
            (r"decider( matches?)?", "решающий матч"), (r"elimination( matches?)?", "матч на вылет"), (r"\bstage\b", "стадия"),
            (r"playoffs?", "плей-офф"), (r"swiss", "швейцарка")]


def round_ru(s: str | None) -> str | None:
    if not s:
        return None
    t = s
    for rx, ru in ROUND_RU:
        t = re.sub(rx, ru, t, flags=re.IGNORECASE)
    return t[:1].upper() + t[1:]


_bo3_tours: dict = {}


def bo3_tour_bg(name: str) -> str | None:
    """Фон карточки турнира: широкий баннер bo3.gg, если есть, иначе обложка турнира."""
    if _bo3_tours.get("list") is None:
        bo3_tournament_for("")  # загрузит список турниров
    nn = tour_norm(name)
    for t in _bo3_tours.get("list") or []:
        tn = (t.get("name") or "").replace("Intel Extreme Masters", "IEM")
        if tour_same(tn, nn) or tour_same(name, tour_norm(tn)):
            return t.get("banner_image_url") or t.get("image_url")
    return None


def bo3_tournament_for(name: str) -> dict | None:
    """Турнир bo3.gg (со стадиями, раундами и матчами) по нашему названию."""
    if _bo3_tours.get("list") is None:
        try:
            _bo3_tours["list"] = bo3("/tournaments", {"page[limit]": 40, "sort": "-start_date", "filter[tournaments.tier][in]": ",".join(sorted(TIERS)),
                                                      "filter[tournaments.status][in]": "upcoming,current,finished"}).get("results") or []
        except Exception as e:
            warn(f"bo3 турниры (итоги): {e!r}")
            _bo3_tours["list"] = []
    res = _bo3_tours["list"]
    if not name:
        return None
    nn = tour_norm(name)
    pick = next((t for t in res if tour_same((t.get("name") or "").replace("Intel Extreme Masters", "IEM"), nn)
                 or tour_same(name, tour_norm((t.get("name") or "").replace("Intel Extreme Masters", "IEM")))), None)
    if not pick:
        return None
    try:
        return bo3(f"/tournaments/{pick['slug']}")
    except Exception as e:
        warn(f"bo3 турнир {pick.get('slug')}: {e!r}")
        return None


def build_results(tr: dict) -> dict | None:
    """Сыгранные матчи турнира со статистикой — для вкладки «Завершённые»."""
    T = bo3_tournament_for(tr["name"])
    if not T:
        return None
    prev_res = load_json(OUT / "results" / (re.sub(r"[^\w-]", "-", str(tr["id"])) + ".json"), {}) or {}
    prev_veto = {str(x.get("id")): x["veto"] for x in prev_res.get("matches") or [] if x.get("veto")}  # найденное вето не теряем
    out, stages = [], []
    for st in sorted(T.get("stages") or [], key=lambda x: x.get("sequence_number") or 0):
        stage = re.sub(re.escape(T.get("name") or "") + r"\s*", "", st.get("title") or "").strip() or None
        rounds = []
        for rd in sorted(st.get("rounds") or [], key=lambda x: ({"upper": 0, "lower": 1}.get(x.get("bracket_type") or "", 2), x.get("round_index") or 0)):
            ms = []
            for m in sorted(rd.get("matches") or [], key=lambda x: x.get("position") or 0):
                t1, t2 = m.get("team1") or {}, m.get("team2") or {}
                ms.append({"id": m.get("id"), "a": t1.get("name"), "b": t2.get("name"), "la": t1.get("image_url"), "lb": t2.get("image_url"),
                           "sa": m.get("team1_score"), "sb": m.get("team2_score"),
                           "st": m.get("status"), "date": m.get("start_date"), "bo": m.get("bo_type"),
                           "w": 0 if m.get("winner_team_id") and m.get("winner_team_id") == m.get("team1_id") else 1 if m.get("winner_team_id") and m.get("winner_team_id") == m.get("team2_id") else None})
            rounds.append({"name": round_ru(rd.get("name")), "bracket": rd.get("bracket_type"), "matches": ms})
        stages.append({"title": round_ru(stage) or "Стадия", "format": st.get("format_type"), "status": st.get("status"), "rounds": rounds})
    for st in T.get("stages") or []:
        stage = re.sub(re.escape(T.get("name") or "") + r"\s*", "", st.get("title") or "").strip() or None
        for rd in st.get("rounds") or []:
            for m in rd.get("matches") or []:
                if m.get("status") != "finished":
                    continue
                m = {**m, "tournament": {"name": T.get("name")}}
                for side in ("team1", "team2"):
                    t = m.get(side) or {}
                    add_alias(t.get("id"), t.get("name"), t.get("slug"), t.get("acronym"))
                try:
                    d = series_detail(m)
                except Exception as e:
                    warn(f"Итог матча {m.get('id')}: {e!r}")
                    d = None
                if d:
                    if prev_veto.get(str(d.get("id"))):
                        d["veto"] = prev_veto[str(d.get("id"))]
                    try:
                        attach_veto(d)
                    except Exception as e:
                        warn(f"Вето итога: {e!r}")
                    d["stage"] = round_ru(stage)
                    d["round"] = round_ru(rd.get("name"))
                    d["tournament"] = tr["name"]
                    out.append(d)
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"updated": iso(NOW), "tournament": tr["name"], "matches": out, "stages": stages}


# ---------------------------------------------------------------- рейтинг Valve (VRS) и призовые места

VRS_REGION = {"europe": "Европа", "americas": "Америка", "eu": "Европа", "am": "Америка", "na": "Америка", "sa": "Америка", "as": "Азия", "asia": "Азия"}


def parse_ranked(h: str) -> list[dict]:
    """Страница рейтинга HLTV (Valve или собственный): место, название, очки, регион, id команды."""
    logos = {}
    for blk in re.sub(r"\s+", " ", h).split('class="ranked-team standard-box"')[1:]:
        lg = re.search(r'<span class="team-logo"><img alt="([^"]+)" src="([^"]+)"', blk)
        if lg:
            logos[strip_html(lg.group(1))] = _html.unescape(lg.group(2))
    b = hclean(h)
    out = []
    for blk in b.split('<div class="ranked-team standard-box">')[1:]:
        pos = re.search(r'class="position[^"]*">#(\d+)<', blk)
        nm = re.search(r'<span class="name">([^<]+)</span>', blk)
        pts = re.search(r'<span class="points">\((\d+)', blk)
        reg = re.search(r'<span class="region region-([a-z]+)">([^<]*)<', blk)
        tid = re.search(r'href="/team/(\d+)/([^"#?]+)"', blk)
        if not (pos and nm and pts):
            continue
        out.append({"rank": int(pos.group(1)), "name": strip_html(nm.group(1)), "points": int(pts.group(1)),
                    "region": (VRS_REGION.get(reg.group(1)) or VRS_REGION.get(reg.group(2).strip().lower()) or reg.group(2)) if reg else None,
                    "hltv": {"id": int(tid.group(1)), "slug": tid.group(2)} if tid else None,
                    "logo": logos.get(strip_html(nm.group(1)))})
    seen: dict = {}
    for r in out:  # место внутри своего региона
        seen[r.get("region")] = seen.get(r.get("region"), 0) + 1
        r["reg_rank"] = seen[r.get("region")]
    return out


def vrs_table() -> list[dict]:
    """Живой рейтинг Valve с HLTV: место, очки, регион (учитывает и незавершённые турниры)."""
    def fn():
        h = hltv_get("/valve-ranking/teams")
        if h:  # на странице рейтинга есть ссылки на игроков составов — запоминаем их id без лишних запросов
            ids = {norm(sl): {"id": int(i), "slug": sl} for i, sl in re.findall(r'href="/player/(\d+)/([^"/?#]+)"', h)}
            if ids:
                hltv_cache["rkpl"] = {"t": iso(NOW), "data": ids}
        return parse_ranked(h) if h else None
    return hltv_cached("vrs3", fn) or []


def hltv_world_ranking() -> list[dict]:
    def fn():
        h = hltv_get("/ranking/teams")
        return parse_ranked(h) if h else None
    return hltv_cached("hltvrank", fn) or []


def valve_md_table() -> list[dict]:
    """Официальный рейтинг Valve с GitHub (снимок раз в месяц) — если на HLTV меньше 100 команд."""
    def fn():
        base = "https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings/contents/live/" + str(NOW.year)
        files = sorted(x["name"] for x in json.loads(http_get(base, {"Accept": "application/vnd.github+json"})) if x.get("type") == "file")
        raw = "https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/%d/" % NOW.year
        glob = [f for f in files if f.startswith("standings_global")][-1]
        region = {}
        for key, ru in (("europe", "Европа"), ("americas", "Америка"), ("asia", "Азия")):
            fs = [f for f in files if f.startswith("standings_" + key)]
            if fs:
                for line in http_get(raw + fs[-1]).decode("utf-8", "replace").splitlines():
                    c = [x.strip() for x in line.split("|")]
                    if len(c) > 4 and c[1].isdigit():
                        region[c[3]] = (ru, int(c[1]))
        out = []
        for line in http_get(raw + glob).decode("utf-8", "replace").splitlines():
            c = [x.strip() for x in line.split("|")]
            if len(c) > 4 and c[1].isdigit():
                rg = region.get(c[3]) or (None, None)
                out.append({"rank": int(c[1]), "name": c[3], "points": int(c[2]), "region": rg[0], "reg_rank": rg[1], "hltv": None,
                            "roster": [x.strip() for x in c[4].split(",") if x.strip()]})
        return {"date": glob[17:27].replace("_", "-"), "teams": out} if out else None
    return hltv_cached("valvemd", fn) or {}


def vrs_find(t: dict, table: list[dict]) -> dict | None:
    keys = []
    for x in (t.get("name"), t.get("acronym"), re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.get("name") or "")):
        if x:
            keys += [norm(x), norm(re.sub(r"^(the|team)\s+|\s+(team|esports|gaming|clan)$", "", x, flags=re.IGNORECASE))]
    keys = [k for k in dict.fromkeys(keys) if k]
    for r in table:
        if norm(r["name"]) in keys:
            return r
    for r in table:
        n = norm(r["name"])
        if any(len(k) >= 4 and len(n) >= 4 and (k in n or n in k) for k in keys):
            return r
    return None


VRS_K = 32  # как в личных встречах модели Valve (сверено с таблицами пересчёта в репозитории Valve)
VALVE_RAW = "https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/"


def valve_curve(x: float) -> float:
    """Кривая Valve для «баунти»: 1 / (1 + |log10 x|), сверено с файлами рейтинга."""
    return 1.0 if x >= 1 else 0.0 if x <= 0 else 1 / (1 + abs(math.log10(x)))


def valve_links() -> dict:
    """norm(команда) → путь к файлу с разбором её очков в официальном рейтинге Valve."""
    def fn():
        base = "https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings/contents/live/" + str(NOW.year)
        files = sorted(x["name"] for x in json.loads(http_get(base, {"Accept": "application/vnd.github+json"})) if x.get("type") == "file")
        glob = [f for f in files if f.startswith("standings_global")][-1]
        out = {}
        for line in http_get(VALVE_RAW + f"{NOW.year}/" + glob).decode("utf-8", "replace").splitlines():
            c = [x.strip() for x in line.split("|")]
            m = re.search(r"\((details/[^)]+)\)", line)
            if len(c) > 4 and c[1].isdigit() and m:
                out.setdefault(norm(c[3]), f"{NOW.year}/" + m.group(1))
        return {"date": glob[17:27].replace("_", "-"), "links": out} if out else None
    return hltv_cached("valvelinks2", fn) or {}


def valve_detail(name: str) -> dict | None:
    links = valve_links().get("links") or {}
    path = links.get(norm(name)) or next((v for k, v in links.items() if len(k) >= 3 and (k in norm(name) or norm(name) in k)), None)
    return valve_detail_path(path) if path else None


def valve_detail_path(path: str) -> dict | None:
    def fn():
        t = http_get(VALVE_RAW + path).decode("utf-8", "replace")
        num = lambda rx: float(re.search(rx, t).group(1))
        d = {"offered": num(r"Bounty Offered:\s*([\d.]+)"), "collected": num(r"Bounty Collected:\s*([\d.]+)"),
             "network": num(r"Opponent Network:\s*([\d.]+)"), "lan": num(r"LAN Wins:\s*([\d.]+)"),
             "seed": num(r"Starting Rank Value \(([\d.]+)\)")}
        mm = re.search(r"400 \+ \( \( [\d.]+ - ([\d.]+) \) / \( ([\d.]+) - [\d.]+ \) \)", t)
        d["min"], d["max"] = (float(mm.group(1)), float(mm.group(2))) if mm else (0.0, 1.0)
        bc, net, lan, opp = [], [], [], {}
        for line in t.split('id="table1"', 1)[-1].split('id="table2"', 1)[0].splitlines():
            c = [x.strip() for x in line.split("|")]
            if len(c) < 12 or not c[1].isdigit():
                continue
            adj = lambda x: float(re.search(r"\(([\d.]+)\)", x).group(1)) if "(" in x else None
            base = lambda x: float(re.match(r"([\d.]+)", x).group(1)) if re.match(r"[\d.]+", x) else None
            for col, lst in ((8, bc), (9, net), (10, lan)):
                v = adj(c[col])
                if v is not None:
                    lst.append(v)
            # «сырые» баунти и сеть соперника (до весов) — нужны, чтобы посчитать, что даст новая победа над ним
            if c[5] == "W":  # «-» — значение не попало в десятку лучших, его не видно; берём только видимые
                o = opp.setdefault(norm(c[4]), {"date": c[3]})
                for f, col in (("bounty", 8), ("network", 9)):
                    v = base(c[col])
                    if v is not None and f not in o:
                        o[f] = v
        d.update({"bc": bc, "net": net, "lanl": lan, "opp": opp})
        return d
    return hltv_cached("valvedet2:" + path, fn)


def valve_bases() -> dict:
    """norm(команда) → её «сырые» баунти и сеть, как их видят победители (из разборов Valve по топ-60)."""
    links = valve_links()
    key = "vbase2:" + (links.get("date") or "")
    c = hltv_cache.get(key)
    if c:
        return c["data"]
    out = {}
    for path in list(dict.fromkeys((links.get("links") or {}).values()))[:60]:
        try:
            dd = valve_detail_path(path) or {}
        except Exception:
            continue
        for k, v in (dd.get("opp") or {}).items():
            o = out.setdefault(k, {})
            for f in ("bounty", "network"):
                if f in v and (f not in o or v["date"] > o.get(f + "_d", "")):
                    o[f], o[f + "_d"] = v[f], v["date"]
    if out:
        hltv_cache[key] = {"t": iso(NOW), "data": out}
    return out


def valve_inv_curve(y: float) -> float:
    """Обратная кривая: из «Bounty Offered» в разборе (после кривой) — сырое значение, которое получает победитель."""
    return 1.0 if y >= 1 else 0.0 if y <= 0 else 10 ** -(1 / y - 1)


def prize_usd(v) -> float:
    m = re.search(r"([\d][\d,. ]*)", str(v or ""))
    try:
        return float(re.sub(r"[^\d]", "", m.group(1))) if m else 0.0
    except ValueError:
        return 0.0


GLICKO_Q = math.log(10) / 400
GLICKO_RD = 75.0  # Valve: setFixedRD(75) — Glicko с постоянным отклонением
GLICKO_G = 1 / math.sqrt(1 + 3 * GLICKO_Q ** 2 * GLICKO_RD ** 2 / math.pi ** 2)


def glicko_delta(r: float, ro: float, score: float, info: float = 1.0) -> float:
    """Изменение очков за одну встречу — ровно как glicko.js в репозитории Valve."""
    ev = 1 / (1 + 10 ** (GLICKO_G * (r - ro) / -400))
    adj = 1 / (1 / GLICKO_RD ** 2 + GLICKO_Q ** 2 * GLICKO_G ** 2 * ev * (1 - ev) * info * info)
    return GLICKO_Q * adj * GLICKO_G * (score - ev) * info


def vrs_outcomes_valve(a: dict, b: dict, table: list[dict], lan: bool = True, hists: list | None = None,
                       bids: list | None = None, prize=None, since: str = "") -> dict | None:
    """Прогноз как у Valve/HLTV: победитель получает очки за личную встречу (Glicko, RD=75, по текущим очкам)
    и за рост своих факторов: баунти соперника и его сеть (сырые значения × вес турнира), LAN-победа.
    Проигравший — только личную встречу. Факторы — из официального разбора Valve на начало месяца."""
    da, db = valve_detail(a["name"]), valve_detail(b["name"])
    if not da or not db:
        return None
    bases = valve_bases()
    def base_of(name, W):
        x = bases.get(norm(name)) or next((v for k, v in bases.items() if len(k) >= 3 and (k in norm(name) or norm(name) in k)), None)
        x = x or {}
        return x.get("bounty", valve_inv_curve(W["offered"])), x.get("network", W["network"])  # запасной вариант
    ew_cur = valve_curve(min(max(prize_usd(prize), 1.0), 1e6) / 1e6) if prize_usd(prize) else 0.5
    da, db = dict(da, bc=list(da["bc"]), net=list(da["net"]), lanl=list(da["lanl"])), dict(db, bc=list(db["bc"]), net=list(db["net"]), lanl=list(db["lanl"]))
    def top(vals, v=None):
        return sum(sorted(vals + ([v] if v is not None else []), reverse=True)[:10]) / 10
    def seed_of(W, extra=None):
        bc = valve_curve(top(W["bc"], extra and extra[0]))
        net = top(W["net"], extra and extra[1])
        ln = top(W["lanl"], extra and extra[2])
        avg = (W["offered"] + bc + net + ln) / 4
        return min(2000.0, 400 + (avg - W["min"]) / max(1e-6, W["max"] - W["min"]) * 1600)
    # победы после снимка Valve (например, на идущем турнире) — добавляем, чтобы факторы были как сейчас
    snap = (valve_links().get("date") or "")
    for W, bid, hist in zip((da, db), bids or [None, None], hists or [[], []]):
        if not bid:
            continue
        for m in hist or []:
            if (m.get("start_date") or "")[:10] < snap or m.get("winner_team_id") != bid:
                continue
            on = opp_of(m, bid).get("name") or ""
            od = valve_detail(on)
            if not od:
                continue
            ob, onet = base_of(on, od)
            same = bool(since) and (m.get("start_date") or "")[:10] >= since[:10]  # матч идущего турнира
            ew = ew_cur if same else 0.5
            W["bc"].append(ob * ew); W["net"].append(onet * ew)
            W["lanl"].append(1.0 if (m.get("tier") or "").lower() == "s" else 0.0)
    for W in (da, db):
        W["seed0"] = seed_of(W)
    def gain(W, L, lname):
        ob, onet = base_of(lname, L)
        return max(0.0, seed_of(W, (ob * ew_cur, onet * ew_cur, 1.0 if lan else 0.0)) - W["seed0"])
    pa0, pb0 = float(a["points"]), float(b["points"])
    res = {}
    for win in (0, 1):
        if win == 0:
            dA, dB = gain(da, db, b["name"]) + glicko_delta(pa0, pb0, 1), glicko_delta(pb0, pa0, 0)
        else:
            dA, dB = glicko_delta(pa0, pb0, 0), gain(db, da, a["name"]) + glicko_delta(pb0, pa0, 1)
        dA, dB = round(dA), round(dB)
        pa, pb = a["points"] + dA, b["points"] + dB
        others = [r["points"] for r in table if r["name"] not in (a["name"], b["name"])]
        ra = 1 + sum(1 for p in others + [pb] if p > pa)
        rb = 1 + sum(1 for p in others + [pa] if p > pb)
        res["win" + str(win)] = [{"delta": dA, "points": pa, "rank": ra}, {"delta": dB, "points": pb, "rank": rb}]
    res["model"] = "valve"
    return res


def vrs_outcomes(a: dict, b: dict, table: list[dict]) -> dict:
    """Оценка: сколько очков VRS и какое место будет у команд при победе каждой (только поправка за личную встречу)."""
    ea = 1 / (1 + 10 ** ((b["points"] - a["points"]) / 400))
    res = {}
    for win in (0, 1):
        da = round(VRS_K * ((1 if win == 0 else 0) - ea))
        db = -da
        pa, pb = a["points"] + da, b["points"] + db
        others = [r["points"] for r in table if r["name"] not in (a["name"], b["name"])]
        ra = 1 + sum(1 for p in others + [pb] if p > pa)
        rb = 1 + sum(1 for p in others + [pa] if p > pb)
        res["win" + str(win)] = [{"delta": da, "points": pa, "rank": ra}, {"delta": db, "points": pb, "rank": rb}]
    return res


PLACE_TOP = re.compile(r"^(1st|2nd|3rd|3-4th)$")


def team_achievements(hltv_team: dict) -> list[dict]:
    """LAN-достижения команды на HLTV: [{'place','event','event_id'}] — от свежих к старым."""
    def fn():
        h = hltv_get(f"/team/{hltv_team['id']}/{hltv_team['slug']}")
        if not h:
            return None
        ids = {}
        for pid, slug, nick in re.findall(r'href="/player/(\d+)/([^"]+)"[^>]*>.{0,400}?<div class="text-ellipsis">([^<]+)</div>', h[:400000], re.S)[:12]:
            ids.setdefault(norm(strip_html(nick)), {"id": int(pid), "slug": slug})
        for pid, slug in re.findall(r'href="/player/(\d+)/([^"/]+)"', h[:400000])[:20]:
            ids.setdefault(norm(slug), {"id": int(pid), "slug": slug})
        hltv_cache[f"tpl:{hltv_team['id']}"] = {"t": iso(NOW), "data": ids}  # id игроков нужны и когда достижения из кэша
        for k, v in ids.items():
            hltv_player_ids.setdefault(k, v)
        i = h.find('id="lanAchievement"')
        if i < 0:
            return []
        sec = h[i:i + 60000].split("</table>", 1)[0]
        out = []
        for pl, eid, ev in re.findall(r'class="text-ellipsis achievement[^"]*">(?:<i[^>]*></i>)?\s*([^<]+?)\s*</div>.*?'
                                      r'<a href="/events/(\d+)/[^"]+">([^<]+)</a>', sec, re.S):
            out.append({"place": pl.strip(), "event": strip_html(ev), "event_id": int(eid)})
        return out
    res = hltv_cached(f"ach:{hltv_team['id']}", fn) or []
    for k, v in ((hltv_cache.get(f"tpl:{hltv_team['id']}") or {}).get("data") or {}).items():
        hltv_player_ids.setdefault(k, v)
    return res


def prize_places(hltv_team: dict | None, hist: list[dict], days: int = 92) -> list[dict]:
    """Призовые места (1–4) за последние 3 месяца: турниры берём из истории матчей bo3 (там даты), места — с HLTV."""
    if not hltv_team:
        return []
    ach = team_achievements(hltv_team)
    if not ach:
        return []
    since = iso(datetime(NOW.year, 1, 1, tzinfo=timezone.utc))  # с 1 января текущего года
    tours: dict = {}
    for m in hist:
        if (m.get("start_date") or "") < since:
            continue
        n = tour_name(m)
        if n and n not in tours:
            tours[n] = m.get("start_date")
    out = []
    for a in ach:
        if not PLACE_TOP.match(a["place"]):
            continue
        ya = re.findall(r"20\d\d", a["event"])
        hit = next((d for n, d in tours.items() if (tour_same(a["event"], tour_norm(n)) or tour_same(n, tour_norm(a["event"])))
                    and (not ya or not re.findall(r"20\d\d", n) or ya[-1] == re.findall(r"20\d\d", n)[-1])), None)
        if hit:
            out.append({"place": a["place"], "event": a["event"], "date": hit})
        elif ya and ya[-1] == str(NOW.year):  # турнира нет в истории bo3, но по названию он этого года — трофей всё равно засчитываем
            out.append({"place": a["place"], "event": a["event"], "date": f"{NOW.year}-01-01"})
    return out


# ---------------------------------------------------------------- имена по-русски с ударением

pron_cache: dict = load_json(PRON_CACHE_F, {})
PRON_SYSTEM = """Ты помогаешь русскоязычному спортивному комментатору правильно произносить имена киберспортсменов CS2.
Для каждого человека дай:
- "name_ru": имя и фамилию по-русски В ПОРЯДКЕ «Имя Фамилия» (сначала имя, потом фамилия, как во входных данных),
  в той форме, как их принято передавать по-русски для этой страны (украинские имена — в русской традиции: Андрей Городенский,
  Валерий Ваховский), БЕЗ знаков ударения;
- "nick_ru": как произносится ник по-русски кириллицей, ОБЯЗАТЕЛЬНО с одним ударением (m0NESY → мо́нэси, ZywOo → зы́ву,
  w0nderful → ва́ндерфул). Цифры читай как буквы, если так принято (b1t → би́т). Если ник — обычное английское слово, дай привычное произношение.
Ничего не выдумывай про людей, только произношение. Ответ строго JSON:
{"people": [{"nick": "<ник как во входе>", "nick_ru": "...", "name_ru": "..."}]}"""


def add_pronunciation(people: list[dict]) -> None:
    """Дописывает nick_ru и name_ru (с ударениями). Одним запросом на команду, результат кэшируется навсегда."""
    todo = []
    for p in people:
        full = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x) or p.get("name") or ""
        key = norm(p.get("nick")) + "|" + norm(full)
        p["_pk"] = key
        if key in pron_cache:
            p.update(pron_cache[key])
        else:
            todo.append((p, full))
    if todo and (GIGA_KEY and not _giga["off"] or llm_ok()):
        def line(p, full):
            fn, ln = p.get("first_name"), p.get("last_name")
            who = f"имя: {fn}; фамилия: {ln}" if fn and ln else f"полное имя: {full or 'неизвестно'}"
            return f"- ник: {p.get('nick')}; {who}; страна: {p.get('nationality') or p.get('flag') or '?'}"
        lines = "\n".join(line(p, full) for p, full in todo)
        # имена — работа GigaChat Max (Gemini остаётся новостям); если GigaChat недоступен — запасной путь через Gemini
        pm = os.environ.get("GIGACHAT_PRON_MODEL", "GigaChat-2-Max")
        res = parse_json_loose(gigachat_call(PRON_SYSTEM, lines, 1200, model=pm)) or parse_json_loose(giga_chat(PRON_SYSTEM, lines, 1200, model=pm))
        got = {norm(x.get("nick")): x for x in (res or {}).get("people") or [] if isinstance(x, dict)}
        for p, full in todo:
            x = got.get(norm(p.get("nick")))
            if x:
                val = {"nick_ru": (x.get("nick_ru") or "").strip() or None, "name_ru": (x.get("name_ru") or "").strip() or None}
                if not full:
                    val["name_ru"] = None
                pron_cache[p["_pk"]] = val
                p.update(val)
    for p in people:
        p.pop("_pk", None)


# ---------------------------------------------------------------- Telegram-каналы

TG_CACHE_F = CACHE / "telegram_posts_v2.json"
tg_cache: dict = load_json(TG_CACHE_F, {})   # "канал/id" → обработанный пост (заголовок, текст с картинки)
TG_GIGA_LIMIT = int(os.environ.get("TG_GIGA_LIMIT", "80"))
_tg_giga = [0]


def tg_fetch_channel(ch: str) -> tuple[str, list[dict]]:
    """Посты публичного канала за NEWS_DAYS дней через веб-версию t.me/s/<канал>."""
    since = NOW - timedelta(days=NEWS_DAYS)
    posts: list[dict] = []
    title = ch
    before = None
    for _ in range(TG_PAGES):
        url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
        try:
            html = http_get(url, {"Accept": "text/html"}, timeout=25).decode("utf-8", "replace")
        except Exception as e:
            warn(f"Telegram @{ch}: {e!r}")
            break
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            title = strip_html(m.group(1))
        blocks = html.split('class="tgme_widget_message_wrap')[1:]
        page = []
        for b in blocks:
            pm = re.search(r'data-post="([^"]+)/(\d+)"', b)
            tm = re.search(r'<time datetime="([^"]+)"', b)
            if not pm or not tm:
                continue
            body = tg_text_html(b)
            text = ""
            if body:
                body = re.sub(r"<(?:blockquote|/blockquote|/p|p)[^>]*>", "<br/>", body)
                text = re.sub(r"<br\s*/?>", " ZZBRZZ ", body)
                text = re.sub(r"\n{3,}", "\n\n", strip_html(text).replace(" ZZBRZZ ", "\n").replace("ZZBRZZ", "\n")).strip()
            photos = re.findall(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
            vthumb = re.findall(r"tgme_widget_message_video_thumb[^>]*background-image:url\('([^']+)'\)", b)
            vsrc = re.search(r'<video[^>]+src="([^"]+)"', b)
            has_video = bool(vsrc or vthumb or "tgme_widget_message_video_player" in b)
            d = parse_iso(tm.group(1))
            page.append({"channel": ch, "id": int(pm.group(2)), "date": d, "text": text,
                         "photo": (photos or vthumb or [None])[0], "photos": photos[:6], "url": f"https://t.me/{ch}/{pm.group(2)}",
                         "video": _html.unescape(vsrc.group(1)) if vsrc else ("tg" if has_video else None)})
        if not page:
            break
        posts += page
        oldest = min(page, key=lambda x: x["id"])
        before = oldest["id"]
        if oldest["date"] and oldest["date"] < since:
            break
        time.sleep(1)
    posts = [p for p in posts if p["date"] and p["date"] >= since]
    return title, posts


def tg_text_html(b: str) -> str | None:
    """Весь текст поста: внутри бывают вложенные блоки (цитаты, эмодзи), поэтому ищем парный закрывающий тег."""
    m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>', b)
    if not m:
        return None
    depth, i = 1, m.end()
    for t in re.finditer(r"<(/?)div\b[^>]*>", b[i:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return b[i:i + t.start()]
    return b[i:]


def giga_upload(data: bytes, name: str = "image.jpg") -> str | None:
    import uuid
    boundary = "----csprep" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\ngeneral\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    try:
        req = urllib.request.Request("https://gigachat.devices.sberbank.ru/api/v1/files", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {giga_token()}",
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60, context=giga_ctx()) as r:
            return json.loads(r.read()).get("id")
    except Exception as e:
        warn(f"GigaChat загрузка картинки: {e!r}")
        return None


def giga_read_image(url: str) -> str | None:
    """Текст с картинки поста (новостные карточки) через GigaChat с распознаванием изображений."""
    if not llm_ok() or not (gem_ok() or giga_budget_ok("max")):
        return None
    try:
        img = http_get(url, {"Accept": "image/*"}, timeout=25)
    except Exception:
        return None
    if len(img) > 9_000_000:
        return None
    if gem_ok():
        import base64
        txt = gemini_call(None, [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}},
                                 {"text": "Перепиши весь текст, который написан на картинке, без пояснений. Если текста нет — ответь одним словом: НЕТ."}],
                          600, json_mode=False)
        if txt:
            txt = txt.strip()
            return None if txt.upper().startswith("НЕТ") else txt
    if not GIGA_KEY or _giga["off"] or not giga_budget_ok("max"):
        return None
    fid = giga_upload(img)
    if not fid:
        return None
    body = json.dumps({"model": os.environ.get("GIGACHAT_VISION_MODEL", "GigaChat-2-Max"), "temperature": 0.1, "max_tokens": 500,
                       "messages": [{"role": "user", "attachments": [fid],
                                     "content": "Перепиши весь текст, который написан на картинке, без пояснений. "
                                                "Если текста нет — ответь одним словом: НЕТ."}]}).encode()
    try:
        req = urllib.request.Request("https://gigachat.devices.sberbank.ru/api/v1/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {giga_token()}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90, context=giga_ctx()) as r:
            j = json.loads(r.read())
        stats["giga_vision"] = stats.get("giga_vision", 0) + 1
        giga_spend("max", (j.get("usage") or {}).get("total_tokens", 0))
        txt = j["choices"][0]["message"]["content"].strip()
        return None if txt.upper().startswith("НЕТ") else txt
    except Exception as e:
        warn(f"GigaChat картинка: {e!r}")
        return None


TG_SYSTEM = """Ты редактор новостей о Counter-Strike 2 для комментатора. Тебе дают пронумерованные посты из Telegram-каналов
(текст поста и, если был, текст с картинки после «[картинка]»). Для КАЖДОГО поста определи категорию — одно слово:
quote — цитата, высказывание, мнение игрока/тренера/персоны (о турнире, сопернике, игре)
interview — интервью
transfer — трансфер, замена, аренда, скамейка, уход, подписание контракта
rumor — слух, инсайд
conflict — конфликт, скандал, наказание, бан, штраф
record — рекорд или антирекорд, «впервые», «лучший/худший за год/в карьере», статистика со сравнением с историей
mvp — гонка за MVP турнира, лучшие игроки турнира по рейтингу
unique_play — по-настоящему уникальный момент: убийство ножом, гранатой, молотовом, зевсом, невероятная стрельба, исторический момент
feature — интересная особенность или забавный факт о человеке или команде (привычки, здоровье, быт)
result — итог матча или турнира, выход дальше, вылет, положение в группе, сетка
stats — обычная статистика игрока или команды после матча без сравнения с историей
highlight — обычный момент: клатч, мультикилл (тройка, квадро, эйс), «посмотрите момент», хайлайт, клип, реакция
schedule — расписание, анонс, превью, прогноз, опрос, «кто победит», напоминание о матче
ad — реклама, промо, скины, обмен скинов, кейсы, ставки, казино, розыгрыш
other — не про CS2 (другая игра) или мем без новости
Заголовок — по-русски до 100 символов, строго по посту, без добавлений. Ники и названия команд латиницей как в оригинале.
Если на картинке цитата с подписью — автор цитаты тот, кто подписан на картинке.
"tour" — турнир, о котором пост (например «PGL Masters Bucharest 2026»), если он назван или очевиден, иначе "".
Ответ строго JSON: {"items": [{"n": 1, "cat": "...", "title": "...", "tour": "..."}]}"""

TG_KEEP = {"quote", "interview", "transfer", "rumor", "conflict", "record", "mvp", "unique_play", "feature", "result"}
TG_CLASSIFY_LIMIT = int(os.environ.get("TG_CLASSIFY_LIMIT", "400"))  # постов на разбор нейросетью за запуск (пачками по 10)
AD = re.compile(r"промокод|реклама|#ad\b|erid|казино|casino|ставк\w*|букмекер|розыгрыш|giveaway|подпишись|cs\.? ?money|csmoney"
                r"|освежи\w* инвент|инвентар\w*|обмен\w* скин\w*|скин\w* (?:на|за) |кейс\w* (?:на|за|с) |фрибет\w*|freebet|бонус\w* (?:за|при|на)"
                r"|депозит\w*|по ссылке|переходи\w*|забирай\w*|скидк\w*|маркетплейс\w*|market\b", re.IGNORECASE)
MOMENT = re.compile(r"клатч\w*|clutch|\b1 ?(?:v|vs|на|в|x|х) ?[2-5]\b|квадр\w*|\bэйс\w*|\bace\b|мультикилл\w*|тройк\w*|\b[345]k\b|флик\w*|"
                    r"переворач\w+ раунд|лучш\w+ (?:карт|перфоманс|раунд)|худш\w+ карт|за карту|на карте|раунд\w*", re.IGNORECASE)
SERIES_RESULT = re.compile(r"плей-?офф|playoff|вылет\w*|покида\w+|выбыва\w+|финал\w*|сетк\w*|чемпион\w*|выигра\w+ (?:турнир|ivent|ивент)|"
                           r"трофе\w*|титул\w*|проход\w+|выход\w* в|вышл\w+|отбор\w*|квалиф\w*|мейджор\w*|major", re.IGNORECASE)
UNIQUE_PLAY = re.compile(r"\bнож\w*|knife|гранат\w*|молотов\w*|зевс\w*|\bzeus\b|тейзер\w*|через смок\w*|wallbang|ноускоп\w*|no-?scope"
                         r"|в прыжке|jump ?shot|впервые в истории|историческ\w+ момент", re.IGNORECASE)


def tg_process(post: dict) -> dict:
    """Текст с картинки (для коротких постов с карточкой). Категорию ставит tg_classify — пачками."""
    key = f"{post['channel']}/{post['id']}"
    info = tg_cache.get(key)
    if info is None:
        info = {"t": iso(NOW)}
        text = post["text"] or ""
        if post.get("photo") and len(text) < 60 and _tg_giga[0] < TG_GIGA_LIMIT and llm_ok():
            _tg_giga[0] += 1
            info["ocr"] = giga_read_image(post["photo"])
        tg_cache[key] = info
    info["title"] = orig_title(post["text"] or "", info.get("ocr") or "")  # заголовок — как в оригинале, без пересказа
    return info


def orig_title(text: str, ocr: str = "") -> str:
    """Первая строка поста (обычно это и есть заголовок канала); длинную режем по концу фразы."""
    for src in (text, ocr):
        line = next((x.strip() for x in (src or "").split("\n") if x.strip()), "")
        if not line:
            continue
        if len(line) <= 160:
            return line
        cut = re.split(r"(?<=[.!?…])\s+", line)
        out = ""
        for c in cut:
            if len(out) + len(c) > 160:
                break
            out = (out + " " + c).strip()
        return out or line[:157].rsplit(" ", 1)[0] + "…"
    return ""


def tg_classify(posts: list[dict]) -> None:
    """Категория, заголовок и турнир для новых постов — по 10 за запрос (свежие первыми)."""
    todo = [p for p in sorted(posts, key=lambda p: p["date"], reverse=True)
            if tg_cache.get(f"{p['channel']}/{p['id']}", {}).get("v") != 3][:TG_CLASSIFY_LIMIT]
    for i in range(0, len(todo), 10):
        if not llm_ok() or not (gem_ok() or giga_budget_ok("lite")):
            return
        chunk = todo[i:i + 10]
        lines = []
        for n, p in enumerate(chunk):
            info = tg_cache.get(f"{p['channel']}/{p['id']}") or {}
            body = re.sub(r"\s+", " ", p["text"] or "")[:600]
            if info.get("ocr"):
                body += " [картинка] " + re.sub(r"\s+", " ", info["ocr"])[:400]
            lines.append(f"{n + 1}. {body or '(пусто)'}")
        res = parse_json_loose(giga_chat(TG_SYSTEM, "\n".join(lines), 1200))
        for it in (res or {}).get("items") or []:
            try:
                n = int(it.get("n")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= n < len(chunk):
                continue
            p = chunk[n]
            info = tg_cache.setdefault(f"{p['channel']}/{p['id']}", {"t": iso(NOW)})
            info["cat"] = str(it.get("cat") or "").strip().lower()
            if it.get("title"):
                info["ai_title"] = str(it["title"]).strip()[:140]  # только для склейки дублей, на сайте не показываем
            info["tour"] = str(it.get("tour") or "").strip() or None
            info["v"] = 3


_tg_all: list[dict] | None = None
_tg_titles: dict = {}
CURRENT_TOURS: list[str] = []   # турниры из нашего расписания — их итоги матчей оставляем


def tg_keep(info: dict, p: dict) -> bool:
    # посты с большим видео оставляем (владелец 05.11): в приложении — обложка и кнопка «Смотреть видео в Telegram»
    text = (info.get("title") or "") + "\n" + (p["text"] or "") + "\n" + (info.get("ocr") or "")
    if AD.search(text[:600]) or SPAM.search(info.get("title") or "") or SPAM.search((p["text"] or "")[:200]):
        return False
    if is_stale_preview(info.get("title"), p["date"]) or is_stale_preview((p["text"] or "").split("\n", 1)[0], p["date"]):
        return False  # превью уже сыгранного матча
    cat = info.get("cat") if info.get("v") == 3 else None
    if cat:
        if cat in ("highlight", "unique_play"):
            return bool(UNIQUE_PLAY.search(text[:400]))  # клатчи, эйсы, квадро — нет; нож, граната, зевс — да
        if cat not in TG_KEEP:
            return False
        rec = re.search(r"рекорд|антирекорд|впервые|худш\w+ (?:в|за) (?:карьер|истори|год)|лучш\w+ (?:в|за) (?:карьер|истори|год)|карьер\w*", text[:400], re.I)
        if MOMENT.search(text[:300]) and not rec and not UNIQUE_PLAY.search(text[:400]) and cat not in ("quote", "interview", "transfer", "rumor", "conflict"):
            return False  # «квадро», «клатч 1v3», «лучшая карта» под видом MVP или итога
        if cat in ("result", "mvp", "record") and is_live_noise(text) and not rec:
            return False  # счёт карты, «размочили счёт», «разгромили на Mirage» — ход матча
        if cat == "result":
            if not SERIES_RESULT.search(text[:400]):
                return False  # итог только серии/турнира: вышли в плей-офф, вылетели, финал
            tour = info.get("tour")
            if not tour:  # турнир не назван нейросетью — смотрим, какой упомянут в тексте
                ms = [m.group(0) for m in re.finditer(r"\b(?:PGL|IEM|BLAST|ESL|StarLadder|Esports World Cup|EWC|FISSURE|CCT|Thunderpick)\b[\w .:-]{0,30}", text[:500])]
                if ms and not any(any(tour_same(x, tour_norm(t)) or tour_same(t, tour_norm(x)) or
                                      any(w in t.lower() for w in re.findall(r"[a-z]{4,}", x.lower()) if w not in ("masters", "major", "league", "season", "world"))
                                      for t in CURRENT_TOURS) for x in ms):
                    return False
                return True
            return not tour or any(tour_same(tour, tour_norm(t)) or tour_same(t, tour_norm(tour)) for t in CURRENT_TOURS)
        return True
    # нейросеть ещё не разобрала — простые правила
    if info.get("news") is False:
        return False
    if UNIQUE_PLAY.search(text[:400]):
        return True
    return not is_live_noise(text) and not (MOMENT.search(text[:300]) and not HAS_SUBSTANCE.search(text[:300]))


def tg_posts() -> list[dict]:
    """Все посты новостных каналов за неделю, уже отобранные и с заголовками (один раз за запуск)."""
    global _tg_all
    if _tg_all is not None:
        return _tg_all
    raw = []
    for ch in TG_NEWS_CHANNELS:
        title, posts = tg_fetch_channel(ch)
        _tg_titles[ch] = title
        for p in posts:
            tg_process(p)
            raw.append(p)
    try:
        tg_classify(raw)
    except Exception as e:
        warn(f"Разбор постов: {e!r}")
    res = []
    for p in raw:
        info = tg_cache.get(f"{p['channel']}/{p['id']}") or {}
        if not tg_keep(info, p):
            continue
        body = p["text"] or info.get("ocr") or ""
        res.append({
            "title": orig_title(p["text"] or "", info.get("ocr") or "") or body[:120], "url": p["url"], "source": f"TG · {_tg_titles.get(p['channel'], p['channel'])}",
            "channel": p["channel"], "date": p["date"], "snippet": body[:400], "text": body, "photo": p.get("photo"),
            "photos": p.get("photos") or [], "video": p.get("video"), "tg": True, "cat": info.get("cat"), "tour": info.get("tour"),
        })
    res.sort(key=lambda x: x["date"], reverse=True)
    _tg_all = res
    return res


def tg_text_preview(text: str, limit: int = 420) -> str:
    t = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


FEED_DEDUP_SYSTEM = """Ты редактор ленты новостей Counter-Strike 2 из нескольких Telegram-каналов. Каналы часто пишут об одном и том же событии разными словами.
Тебе дают пронумерованные посты: время, канал, заголовок и начало текста.
Найди группы постов, которые сообщают ОБ ОДНОМ И ТОМ ЖЕ событии (одна новость, одна цитата, один трансфер, один итог), даже если формулировки разные.
Разные события про одну команду или игрока — не объединяй (например, «команда проиграла» и «команда сменила состав» — разные).
В "stale" перечисли номера устаревших постов: более ранний пост, которого обогнал более поздний про ту же историю
(«в шаге от MVP» → «получил MVP», «может перейти» → «перешёл»). Устаревшим может быть только более ранний по времени.
Ответ строго JSON: {"groups": [[номера], [номера]], "stale": [номера]}. В groups — только группы из двух и более постов."""


def feed_dedupe(clusters: list[list[dict]]) -> list[list[dict]]:
    """Второй проход по ленте: GigaChat склеивает одну новость из разных каналов. Окна по 40 постов с перекрытием."""
    if not llm_ok() or len(clusters) < 2:
        return clusters
    clusters = sorted(clusters, key=lambda cl: max(x["date"] for x in cl), reverse=True)
    parent = list(range(len(clusters)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    W, STEP = 40, 30
    stale: set = set()
    for start in range(0, 1 if FAST else max(1, min(len(clusters), 110) - 10), STEP):  # быстрый режим — только свежие 40  # свежие ~110 постов: дубли бывают рядом по времени
        idx = list(range(start, min(len(clusters), start + W)))
        if len(idx) < 2:
            break
        lines = []
        for n, i in enumerate(idx):
            p = clusters[i][0]
            body = re.sub(r"\s+", " ", p.get("text") or "")
            if body.startswith(p["title"][:40]):
                body = body[len(p["title"]):].strip()
            body = body[:140]
            lines.append(f"{n + 1}. [{p['date'].strftime('%d.%m %H:%M')}, {p['channel']}] {p['title']} — {body}")
        text = "\n".join(lines)
        sig = hashlib.sha1(text.encode()).hexdigest()[:16]
        res = (dedup_cache.get("feed:" + sig) or {}).get("res")
        if res is None:
            res = parse_json_loose(giga_chat(FEED_DEDUP_SYSTEM, text, 1400))
            if res is None:
                continue
            dedup_cache["feed:" + sig] = {"res": res, "t": iso(NOW)}
        for j in stale_ids(res, [clusters[i][0]["date"] for i in idx]):
            stale.add(idx[j])
        for g in res.get("groups") or []:
            ids = [idx[int(x) - 1] for x in g if str(x).isdigit() and 0 < int(x) <= len(idx)]
            ids = [i for i in ids if abs((clusters[i][0]["date"] - clusters[ids[0]][0]["date"]).total_seconds()) < 48 * 3600] if ids else []
            for i in ids[1:]:
                parent[find(i)] = find(ids[0])
    groups: dict = {}
    for i in range(len(clusters)):
        if i in stale and find(i) == i and not any(find(k) == i for k in range(len(clusters)) if k != i):
            continue  # устаревший одиночный пост — убираем
        groups.setdefault(find(i), []).extend(clusters[i])
    out = list(groups.values())
    for cl in out:
        cl.sort(key=src_rank)
    out.sort(key=lambda cl: max(x["date"] for x in cl), reverse=True)
    return out


def _toks(t: str) -> set:
    return set(re.findall(r"[a-zа-яё0-9]{3,}", (t or "").lower()))


def dedupe_strict(items: list[dict]) -> list[list[dict]]:
    """Склеиваем только повторы одной и той же новости (почти тот же текст, в пределах 2 суток).
    Из повторов первой идёт самая подробная (длиннее текст). Фото и текст всегда от одного поста."""
    clusters: list[list[dict]] = []
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        tk = _toks((it.get("text") or "") + " " + (it.get("title") or ""))
        hit = None
        for cl in clusters:
            r = cl[0]
            if abs((r["date"] - it["date"]).total_seconds()) > 48 * 3600:
                continue
            rk = _toks((r.get("text") or "") + " " + (r.get("title") or ""))
            small = min(len(tk), len(rk)) or 1
            if len(tk & rk) / small >= 0.75 and len(tk & rk) >= 6:  # одна новость, перепечатанная или чуть сокращённая
                hit = cl
                break
        if hit is None:
            clusters.append([it])
        else:
            hit.append(it)
            hit.sort(key=lambda x: len(x.get("text") or ""), reverse=True)  # подробная — главная
    clusters.sort(key=lambda cl: max(x["date"] for x in cl), reverse=True)
    return clusters


def build_feed() -> dict:
    """Лента на главном экране: посты каналов без повторов."""
    posts = [dict(p, about=["CS"]) for p in tg_posts()]
    for p in posts:
        p["key"] = "tg:" + p["url"].split("t.me/")[1]
        p["title_ru0"], p["lang0"] = None, "ru"
    clusters = dedupe_strict(posts)  # не объединяем разные посты про одно и то же — только повторы (владелец 05.11)
    items = []
    for cl in clusters[:150]:
        it, others = cl[0], cl[1:]
        items.append({
            "key": it["key"], "title": it["title"], "text": tg_text_preview(it["text"]), "full": tg_text_preview(it["text"], 100000),  # весь текст поста
            "photo": it.get("photo"), "photos": it.get("photos") or [], "video": it.get("video"),
            "channel": it["channel"], "channel_title": _tg_titles.get(it["channel"], it["channel"]),
            "url": it["url"], "date": iso(it["date"]),
            "also": [{"channel": o["channel"], "channel_title": _tg_titles.get(o["channel"], o["channel"]), "url": o["url"]}
                     for o in others],
        })
    return {"updated": iso(NOW), "channels": [{"id": c, "title": _tg_titles.get(c, c)} for c in TG_NEWS_CHANNELS],
            "items": items}


# ---------------------------------------------------------------- HLTV: призовые, слоты, мейджор

HLTV = "https://www.hltv.org"
HLTV_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HLTV_CACHE_F = CACHE / "hltv.json"
hltv_cache: dict = load_json(HLTV_CACHE_F, {})
HLTV_TTL_H = 6
_hltv_pages: dict = {}
_hltv_last = [0.0]


_hltv_blocked = [False]


_hltv_403 = [0]  # отказов подряд


HLTV_BACKOFF_DAYS = [1, 3, 7]  # страница отказала 1-й, 2-й, 3-й+ раз — не просим её столько дней


def hltv_page_skip(path: str) -> bool:
    """Страница недавно отвечала отказом — не тратим на неё запрос (пауза растёт с каждым отказом)."""
    f = hltv_cache.get("hfail:" + path)
    if not f:
        return False
    days = HLTV_BACKOFF_DAYS[min(f.get("n", 1), len(HLTV_BACKOFF_DAYS)) - 1]
    return (parse_iso(f.get("t")) or NOW - timedelta(days=30)) > NOW - timedelta(days=days)


def hltv_get(path: str) -> str | None:
    if path in _hltv_pages:
        return _hltv_pages[path]
    if _hltv_blocked[0]:
        return None
    if hltv_page_skip(path):
        print(f"HLTV пауза: {path} (недавно отказывала)")
        _hltv_pages[path] = None
        return None
    html = None
    for attempt in (0, 1):
        wait = 2.5 - (time.time() - _hltv_last[0])
        if wait > 0:
            time.sleep(wait)
        _hltv_last[0] = time.time()
        stats["hltv_requests"] = stats.get("hltv_requests", 0) + 1
        try:
            html = http_get(HLTV + path, {"User-Agent": HLTV_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                            timeout=30).decode("utf-8", "replace")
            _hltv_403[0] = 0
            hltv_cache.pop("hfail:" + path, None)
            break
        except urllib.error.HTTPError as e:
            warn(f"HLTV {path}: {e!r}")
            if e.code in (429, 503) and attempt == 0:
                time.sleep(60)  # «слишком часто» — минуту ждём и пробуем один раз ещё
                continue
            if e.code in (429, 503):
                _hltv_blocked[0] = True  # повторный — HLTV просит остановиться, до следующего запуска не обращаемся
                break
            if e.code == 403:
                # единичный отказ — пропускаем только эту страницу (с растущей паузой), два подряд — стоп до следующего запуска
                f = hltv_cache.get("hfail:" + path) or {}
                hltv_cache["hfail:" + path] = {"t": iso(NOW), "n": f.get("n", 0) + 1}
                _hltv_403[0] += 1
                if _hltv_403[0] >= 3:  # три разные страницы подряд отказали — HLTV явно против, стоп до следующего запуска
                    _hltv_blocked[0] = True
            break
        except Exception as e:
            warn(f"HLTV {path}: {e!r}")
            break
    _hltv_pages[path] = html
    return html

def hclean(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<img[^>]*>|<svg.*?</svg>|<source[^>]*>", "", h, flags=re.S))


_events_cache: dict = {}


def hltv_events() -> dict:
    """slug → путь страницы события: текущие и будущие (/events) + сыгранные в этом году (архив, раз в сутки)."""
    if _events_cache:
        return _events_cache
    out = {}
    for href, slug in re.findall(r'href="(/events/\d+/([^"#?]+))"', hltv_get("/events") or ""):
        out.setdefault(slug, href)

    def arch():  # архив листается страницами по 50 — берём до 6 страниц (весь год), раз в сутки
        res: dict = {}
        for off in range(0, 300, 50):
            h = hltv_get(f"/events/archive?startDate={NOW.year}-01-01&endDate={NOW.date().isoformat()}" + (f"&offset={off}" if off else ""))
            got = dict((slug, href) for href, slug in re.findall(r'href="(/events/\d+/([^"#?]+))"', h or ""))
            new = {k: v for k, v in got.items() if k not in res}
            res.update(new)
            if len(new) < 10:
                break
        print(f"Архив событий HLTV за год: {len(res)}")
        return res or None
    a = {}  # архив сыгранных не читаем: обзор сыгранного турнира — из данных, собранных, пока он шёл
    for slug, href in a.items():
        out.setdefault(slug, href)
    _events_cache.update(out)
    print(f"События HLTV: текущих и будущих + архив = {len(out)}")
    return out


def money(s: str | None) -> int | None:
    if not s:
        return None
    d = re.sub(r"[^\d]", "", s)
    return int(d) if d else None


def parse_hltv_event(html: str) -> dict:
    b = hclean(html)
    res: dict = {}
    m = re.search(r'class="prizepool text-ellipsis" title="([^"]+)"', b)
    res["prize"] = money(m.group(1)) if m else None
    m = re.search(r"Player Share:</div><div[^>]*>([^<]+)<", b)
    res["player_share"] = money(m.group(1)) if m else None
    m = re.search(r"Club Share:</div><div[^>]*>([^<]+)<", b)
    res["club_share"] = money(m.group(1)) if m else None
    places = []
    i = b.find('id="PrizeDistribution"')
    if i >= 0:
        sec = b[i:i + 40000]
        for pl in re.findall(r'<div class="placement">(.*?)</div></div>(?=<div class="col|</div></div>)', sec):
            place = re.search(r'<div class="team">.*?</div><div>([^<]+)</div>', pl)
            prizes = re.findall(r'<div class="prize[^"]*">(.*?)</div>', pl)
            team = re.search(r'<div class="team">(.*?)</div>', pl)
            tname = strip_html(team.group(1)) if team else ""
            money_main = strip_html(prizes[0]) if prizes else ""
            club = next((strip_html(x) for x in prizes if "Club share" in x), "")
            spots = [strip_html(x) for x in re.findall(r'class="spot-prize">(.*?)</a>', pl)]
            other = [strip_html(x) for x in prizes[1:] if x and "Club share" not in x and "spot-prize" not in x and strip_html(x)]
            places.append({"place": place.group(1).strip() if place else "", "prize": money(money_main),
                           "club": money(club), "spots": spots, "other": other, "team": tname or None})
    # одинаковые места подряд (5-6th ×2) — схлопываем
    merged = []
    for p in places:
        if merged and merged[-1]["place"] == p["place"] and merged[-1]["prize"] == p["prize"] and merged[-1]["spots"] == p["spots"]:
            merged[-1]["count"] += 1
            if p.get("team"):
                merged[-1]["teams"].append(p["team"])
            continue
        merged.append({**p, "count": 1, "teams": [p["team"]] if p.get("team") else []})
    res["places"] = merged
    teams = re.findall(r'<div class="team-name"><a href="/team/\d+/[^"]+"><div class="text-container"><div class="text">([^<]+)</div>'
                       r'.*?<div class="sub-text event-text">(.*?)</div>', b)
    res["teams"] = [{"name": strip_html(n), "how": strip_html(h)} for n, h in teams]
    lineups = {}
    for box in b.split('class="col standard-box team-box')[1:]:
        tn = re.search(r'<div class="text">([^<]+)</div>', box)
        if not tn:
            continue
        lu = box.split('class="lineup-box', 1)
        pl = re.findall(r'<a href="/player/(\d+)/([^"]+)">([^<]+)</a>', lu[1] if len(lu) > 1 else "")
        lineups[strip_html(tn.group(1))] = [{"id": int(i), "slug": sl, "nick": strip_html(nk)} for i, sl, nk in pl]
    res["lineups"] = lineups
    res["team_ids"] = {strip_html(n): {"id": int(i), "slug": sl} for i, sl, n in
                       re.findall(r'<div class="team-name"><a href="/team/(\d+)/([^"]+)"><div class="text-container"><div class="text">([^<]+)</div>', b)}
    return res


def hltv_event_for(name: str, events: dict) -> str | None:
    """Событие HLTV по названию: все слова — точное совпадение; иначе лучшее по доле слов при совпадении года
    (у HLTV бывает «blast-rivals-2026-season-2» вместо «BLAST Rivals Fall 2026»)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in ("the",)]
    toks = ["iem" if t == "intel" else t for t in toks if t not in ("extreme", "masters")] if "intel" in toks else toks
    best = None
    for slug, href in events.items():
        st = slug.split("-")
        if all(t in st for t in toks) and (best is None or len(slug) < len(best[0])):
            best = (slug, href)
    if best:
        return best[1]
    year = next((t for t in toks if re.fullmatch(r"20\d\d", t)), None)
    soft = [t for t in toks if t not in ("fall", "spring", "summer", "winter", "season", "finals", "final") and not re.fullmatch(r"20\d\d", t)]
    cand = []
    for slug, href in events.items():
        st = slug.split("-")
        if year and year not in st or not soft:
            continue
        sc = sum(1 for t in soft if t in st) / len(soft)
        if sc >= 0.75 and not any(x in st for x in ("qualifier", "qualification", "closed", "open")):
            cand.append((-sc, len(slug), href))
    return min(cand)[2] if cand else None


def hltv_cached(key: str, fn):
    c = hltv_cache.get(key)
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=HLTV_TTL_H):
        return c["data"]
    try:
        data = fn()
    except Exception as e:
        warn(f"HLTV {key}: {e!r}")
        data = None
    if data:
        hltv_cache[key] = {"t": iso(NOW), "data": data}
        return data
    return c["data"] if c else None


def tournament_extras(name: str, final: bool = False) -> dict | None:
    c = hltv_cache.get("event3:" + name)
    if final and c and c.get("data"):
        return c["data"]  # у сыгранного турнира призовые и участники уже не меняются
    def fn():
        href = hltv_event_for(name, hltv_events())
        if not href:
            return None
        h = hltv_get(href)
        return {**parse_hltv_event(h), "hltv": HLTV + href} if h else None
    return hltv_cached("event3:" + name, fn)


def parse_vrs_major(html: str) -> dict:
    """Прогноз HLTV по приглашениям на мейджор: регион → секции (Stage 3/2/1, дальше очередь) → команды."""
    b = hclean(html)
    regions = []
    parts = re.split(r'<div class="valve-ranking-region-header">', b)[1:]
    for part in parts:
        rname = strip_html(part.split("<span", 1)[0])
        sections = []
        for sp in re.split(r'<div class="valve-ranking-region-spot ', part)[1:]:
            cls = sp.split('"', 1)[0]
            head = re.search(r'valve-ranking-region-spot-header">(.*?)<div class="spot-divider"', sp)
            title = strip_html(head.group(1)) if head else cls
            teams = []
            for pos, nm, pts, players in re.findall(
                    r'<span class="position[^"]*">#(\d+)</span>.*?<span class="name">([^<]+)</span><span class="points">\((\d+)'
                    r'.*?<div class="playersLine ">(.*?)</div></div></div>', sp):
                teams.append({"pos": int(pos), "name": strip_html(nm), "points": int(pts),
                              "roster": [strip_html(x) for x in re.findall(r"<span>([^<]+)</span>", players)]})
            sections.append({"key": cls.strip(), "title": title, "teams": teams})
        regions.append({"region": rname, "sections": sections})
    m = re.search(r'regional-ranking-header-text">(.*?)<div', b)
    return {"title": strip_html(m.group(1)) if m else None, "regions": regions}


HLTV_STATS_CACHE_F = CACHE / "hltv_player_stats.json"
hltv_stats_cache: dict = load_json(HLTV_STATS_CACHE_F, {})
HLTV_STATS_LIMIT = int(os.environ.get("HLTV_STATS_LIMIT", "0"))  # страницы статистики HLTV закрыты для ботов (403)
_hltv_stats_new = [0]
hltv_player_ids: dict = {}   # norm(ник) → {"id","slug"}


def _num_after(b: str, label: str) -> float | None:
    i = b.find(label)
    if i < 0:
        return None
    m = re.search(r">\s*(\d+(?:\.\d+)?)\s*%?\s*<", b[i:i + 600])
    return float(m.group(1)) if m else None


def hltv_player_stats(nick: str) -> dict | None:
    """Rating 3.0, K/D, ADR, KAST игрока на HLTV за 3 месяца. Кэш на сутки."""
    pid = hltv_player_ids.get(norm(nick))
    if not pid:
        return None
    key = str(pid["id"])
    c = hltv_stats_cache.get(key)
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=24):
        return c["data"]
    if _hltv_stats_new[0] >= HLTV_STATS_LIMIT:
        return c["data"] if c else None
    _hltv_stats_new[0] += 1
    start = (NOW - timedelta(days=90)).strftime("%Y-%m-%d")
    end = NOW.strftime("%Y-%m-%d")
    h = hltv_get(f"/stats/players/{pid['id']}/{pid['slug']}?startDate={start}&endDate={end}")
    if not h:
        return c["data"] if c else None
    b = hclean(h)
    data = {"rating": _num_after(b, "Rating 3.0"), "kd": _num_after(b, "K/D Ratio"),
            "adr": _num_after(b, "ADR") or _num_after(b, "Damage / Round"), "kast": _num_after(b, "KAST"),
            "url": HLTV + f"/stats/players/{pid['id']}/{pid['slug']}"}
    if data["rating"] is None and data["kd"] is None:
        warn(f"HLTV статистика {nick}: не нашёл цифры на странице")
        return c["data"] if c else None
    hltv_stats_cache[key] = {"t": iso(NOW), "data": data}
    return data


def build_major(rosters: dict) -> dict | None:
    def fn():
        inv = hltv_get("/valve-ranking/invites") or ""
        evs = re.findall(r'<a href="(/valve-ranking/teams/event/\d+)" class="ranking-vrs-event">.*?ranking-vrs-event-header">([^<]+)<'
                         r'.*?ranking-vrs-event-date">([^<]+)<', inv, re.S)
        maj = next(((h, n, d) for h, n, d in evs if "major" in n.lower()), None)
        if not maj:
            return None
        page = hltv_get(maj[0])
        if not page:
            return None
        data = parse_vrs_major(page)
        data["invite_date"] = maj[2].strip()
        events = hltv_events()
        base = next((s for s in events if "major" in s and re.search(r"20\d\d$", s)), None)
        stages = {}
        if base:
            for key, slug in (("stage1", base + "-stage-1"), ("stage2", base + "-stage-2"), ("main", base)):
                if slug in events:
                    h = hltv_get(events[slug])
                    if h:
                        ev = parse_hltv_event(h)
                        stages[key] = {"teams": ev["teams"], "places": ev["places"], "prize": ev.get("prize"), "url": HLTV + events[slug]}
            data["event"] = base
        data["stages"] = stages
        return data
    data = hltv_cached("major", fn)
    if not data:
        return None
    # смена состава после снимка: сравниваем с актуальными составами, которые у нас есть
    for reg in data.get("regions") or []:
        for sec in reg["sections"]:
            for t in sec["teams"]:
                cur = rosters.get(norm(t["name"]))
                if cur:
                    old = {norm(x): x for x in t["roster"]}
                    new = {norm(x): x for x in cur}
                    out_, in_ = [old[k] for k in old if k not in new], [new[k] for k in new if k not in old]
                    t["roster_change"] = {"out": out_, "in": in_} if out_ or in_ else None
    return data


REG_RU = {"Europe": "Европа", "Americas": "Америка", "Asia": "Азия"}


def major_view(raw: dict | None) -> dict | None:
    """Прогноз HLTV по VRS → стадии с оценкой, насколько надёжно команда держится в своей стадии."""
    if not raw or not raw.get("regions"):
        return None
    order = ["Stage 3", "Stage 2", "Stage 1"]
    stages = {k: [] for k in order}
    bubble = []
    for reg in raw["regions"]:
        rname = REG_RU.get(reg.get("region"), reg.get("region"))
        secs = reg.get("sections") or []
        for i, sec in enumerate(secs):
            m = re.search(r"stage[\s-]*([123])", (sec.get("key") or "") + " " + (sec.get("title") or ""), re.IGNORECASE)
            below = next((x["teams"][0] for x in secs[i + 1:] if x.get("teams")), None)  # первая команда ниже этой стадии
            for t in sec.get("teams") or []:
                if m:
                    gap = t["points"] - below["points"] if below else None
                    chance = None if gap is None else "уверенно" if gap >= 100 else "вероятно" if gap >= 30 else "на грани"
                    stages["Stage " + m.group(1)].append({"name": t["name"], "region": rname, "pos": t.get("pos"),
                                                          "points": t.get("points"), "gap": gap, "chance": chance,
                                                          "roster_change": t.get("roster_change")})
                else:
                    last = next((x["teams"][-1] for x in reversed(secs[:i]) if x.get("teams") and re.search(r"stage", (x.get("key") or "") + (x.get("title") or ""), re.I)), None)
                    gap = last["points"] - t["points"] if last else None
                    if gap is not None and gap <= 60 and len([b for b in bubble if b["region"] == rname]) < 3:
                        bubble.append({"name": t["name"], "region": rname, "pos": t.get("pos"), "points": t.get("points"), "gap": gap})
    if not any(stages.values()):
        return None
    return {"final": False, "invite_date": raw.get("invite_date"), "title": raw.get("title"),
            "stages": [{"stage": k, "teams": stages[k]} for k in order if stages[k]], "bubble": bubble,
            "source": "https://www.hltv.org/valve-ranking/invites"}


def attach_major(tour_list: list, rosters: dict) -> None:
    """Мейджор в нашем списке турниров → кто с какой стадии начинает (итог) или прогноз по VRS."""
    majors = [t for t in tour_list if re.search(r"\bmajor\b", t.get("name") or "", re.IGNORECASE)]
    if not majors:
        return
    manual = load_json(Path(__file__).with_name("major_manual.json"), None)
    proj = None
    for t in majors:
        if manual and tour_same(t["name"], tour_norm(manual.get("event"))):
            t["major"] = {k: manual[k] for k in ("final", "invite_date", "stages", "source") if k in manual}
            continue
        if proj is None:
            try:
                proj = major_view(build_major(rosters)) or {}
            except Exception as e:
                warn(f"Мейджор: {e!r}")
                proj = {}
        if proj:
            t["major"] = proj  # прогноз VRS — для ближайшего мейджора без итоговых приглашений
            break


# ---------------------------------------------------------------- вето (пики и баны) с HLTV

VETO_CACHE_F = CACHE / "hltv_vetoes.json"
veto_cache: dict = load_json(VETO_CACHE_F, {})   # "match:<id>" → вето навсегда; "results:<team>" → список матчей на 12 ч
VETO_NEW_LIMIT = int(os.environ.get("VETO_NEW_LIMIT", "1500"))  # вето берём с dust2.us (сайт-партнёр HLTV, та же база)
D2 = "https://www.dust2.us"
_d2_blocked = [False]
_d2_last = [0.0]


def d2_get(path: str) -> str | None:
    if _d2_blocked[0]:
        return None
    wait = 1.0 - (time.time() - _d2_last[0])  # dust2 отвечает без отказов; раз в секунду — бережно, но быстро
    if wait > 0:
        time.sleep(wait)
    _d2_last[0] = time.time()
    stats["dust2_requests"] = stats.get("dust2_requests", 0) + 1
    try:
        return http_get(D2 + path, {"User-Agent": HLTV_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                        timeout=40).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            _d2_blocked[0] = True
        warn(f"dust2 {path}: {e!r}")
    except Exception as e:
        warn(f"dust2 {path}: {e!r}")
    return None
VETO_MATCHES = 20    # для сводки «что пикает / банит» хватает последних 20
VETO_LIST = VETO_MATCHES
_veto_new = [0]
hltv_team_ids: dict = {}   # norm(название) → {"id","slug","name"}


def hltv_team_for(t: dict) -> dict | None:
    for k in (t.get("name"), t.get("acronym"), re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.get("name") or "")):
        x = hltv_team_ids.get(norm(k))
        if x:
            return x
    n = norm(t.get("name"))
    for k, v in hltv_team_ids.items():
        if n and (n in k or k in n) and min(len(n), len(k)) >= 3:
            return v
    return None


def hltv_team_matches(team: dict) -> list[dict]:
    key = f"results4:{team['id']}"
    c = veto_cache.get(key)
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=12):
        return c["data"]
    # dust2.us перестал фильтровать результаты по команде — список матчей берём с HLTV (страница результатов открыта),
    # а само вето по-прежнему со страницы матча dust2 (тот же id)
    h = hltv_get(f"/results?team={team['id']}")
    if not h:
        return c["data"] if c else []
    out = []
    own = norm(team.get("slug") or team.get("name"))
    # дата матча — из заголовка блока «Results for November 4th 2026», под которым он стоит
    heads = [(m.start(), m.group(1), m.group(2), m.group(3)) for m in
             re.finditer(r'Results for (\w+) (\d+)(?:st|nd|rd|th) (\d{4})', h)]
    for mm in re.finditer(r'href="/matches/(\d+)/([^"#?]+)"', h):
        mid, slug = mm.group(1), mm.group(2)
        if own and own not in norm(slug):
            continue  # блоки «избранное» и чужие матчи на странице
        if mid not in [x["id"] for x in out]:
            day = None
            hd = [x for x in heads if x[0] < mm.start()]
            if hd:
                try:
                    day = datetime.strptime(f"{hd[-1][1]} {hd[-1][2]} {hd[-1][3]}", "%B %d %Y").date().isoformat()
                except ValueError:
                    day = None
            out.append({"id": mid, "slug": slug, "day": day})
        if len(out) >= VETO_LIST:
            break
    veto_cache[key] = {"t": iso(NOW), "data": out}
    return out


def hltv_veto(match: dict) -> dict | None:
    """Вето матча со страницы dust2.us: по порядку карты BANNED / PICKED / оставшаяся. Хранится навсегда."""
    key = f"match:{match['id']}"
    c = veto_cache.get(key)
    if c and any(x.get("team") for x in c.get("steps") or []):
        return c  # без команд у банов/пиков — старая разборка, перечитываем
    if _veto_new[0] >= VETO_NEW_LIMIT:
        return None
    _veto_new[0] += 1
    h = d2_get(f"/matches/{match['id']}/{match['slug']}")
    if not h:
        return None
    b = re.sub(r"\s+", " ", h)
    steps = d2_parse_veto(b)
    dm = re.search(r'data-(?:unix|date)="(\d{12,})"', b)
    d = {"date": iso(datetime.fromtimestamp(int(dm.group(1)) / 1000, tz=timezone.utc)) if dm else None, "steps": steps}
    if not d["date"] and match.get("date"):
        d["date"] = match["date"]  # дата из ленты dust2
    if not d["date"] and match.get("day"):
        d["date"] = match["day"] + "T12:00:00Z"
    if steps:
        veto_cache[key] = d   # вето сыгранного матча не меняется
    return d


def d2_parse_veto(b: str) -> list:
    """Шаги вето со страницы матча dust2.us (b — html с одинарными пробелами)."""
    steps = []
    i = b.find('class="maps-container"')
    sec = b[i:i + 40000] if i >= 0 else ""
    for n, box in enumerate(re.split(r'<div class="standard-box map-container ', sec)[1:], 1):
        nm = re.search(r'map-container-map-name">([^<]+)<', box)
        tp = re.search(r'map-container-type">([^<]+)<', box)
        team = (re.search(r'class="team-logo[^"]*" title="([^"]+)"', box)
                or re.search(r'map-container-team-logo-container">\s*<span[^>]*>([^<]+)</span>', box))
        if not nm or strip_html(nm.group(1)).strip().upper() in ("TBA", "TBD", "-", ""):
            continue
        t = (tp.group(1).strip().upper() if tp else "")
        act = "ban" if "BAN" in t else "pick" if "PICK" in t else "decider"
        steps.append({"n": n, "team": strip_html(team.group(1)) if team and act != "decider" else None, "act": act,
                      "map": strip_html(nm.group(1))})
    return steps


def d2_upcoming() -> list[dict]:
    """Ближайшие матчи с dust2.us /matches (одна страница): id, slug, время начала (как на HLTV), команды."""
    h = d2_get("/matches")
    if not h:
        return []
    b = re.sub(r"\s+", " ", h)
    out, seen = [], set()
    for blk in re.split(r'<div class="match-list-component|<div class="match text-ellipsis', b)[1:]:
        mm = re.search(r'href="/matches/(\d+)/([^"#?]+)"', blk)
        if not mm:
            continue
        if mm.group(1) in seen:
            continue
        seen.add(mm.group(1))
        dd = re.search(r'data-date="(\d{12,})"', blk)
        teams = list(dict.fromkeys(re.findall(r'match-team-name[^"]*">([^<]+)<', blk)
                                   or re.findall(r'class="team-logo[^"]*" title="([^"]+)"', blk)
                                   or re.findall(r'<img alt="([^"]+)"[^>]*class="team-logo', blk)))
        ph = re.findall(r'team-placeholder">([^<]+)<', blk)
        out.append({"id": mm.group(1), "slug": mm.group(2), "teams": [strip_html(x) for x in teams][:2], "ph": [strip_html(x) for x in ph],
                    "begin": iso(datetime.fromtimestamp(int(dd.group(1)) / 1000, tz=timezone.utc)) if dd else None})
    return out


def d2_find(lst: list[dict], teams: list[dict], begin: str | None) -> dict | None:
    def keys(t):
        return {k for k in (norm(t.get("name")), norm(t.get("acronym")), norm(re.sub(r"(?i)^team |\s+(team|esports|gaming|club)$", "", t.get("name") or ""))) if k}
    want = [keys(t) for t in teams]
    bt = parse_iso(begin)
    for x in lst:
        got = [norm(n) for n in x.get("teams") or []] + [norm(x.get("slug"))]
        ok = all(any(k == g or (len(k) >= 4 and k in g) or (len(g) >= 4 and g in k) for k in w for g in got) for w in want)
        if ok and len(want) == 1 and not x.get("ph"):
            ok = False  # у нас одна команда — ищем матч, где у dust2 тоже «победитель X/Y»
        xt = parse_iso(x.get("begin"))
        if ok and (not bt or not xt or abs((xt - bt).total_seconds()) < 14 * 3600):
            return x
    return None


def team_names_index(listing: list | None) -> dict:
    """norm(название/аббревиатура) → {name, acronym, image} по всем известным командам (список матчей и карточки)."""
    idx: dict = {}
    def put(t):
        if not t or not t.get("name"):
            return
        v = {"name": t["name"], "acronym": t.get("acronym"), "image": t.get("image")}
        for k in (t.get("name"), t.get("acronym"), re.sub(r"(?i)^(the|team)\s+", "", t.get("name") or "")):
            if norm(k):
                idx.setdefault(norm(k), v)
    for m in listing or (load_json(OUT / "matches.json", {}) or {}).get("matches") or []:
        for t in m.get("teams") or []:
            put(t)
    for f in (OUT / "match").glob("*.json"):
        for t in (load_json(f, {}) or {}).get("teams") or []:
            put(t)
    idx.setdefault("navi", idx.get("natusvincere"))
    return {k: v for k, v in idx.items() if v}


def tbd_opp(ph: str, idx: dict) -> dict | None:
    """«PARIVISION/NaVi winner» → кандидаты в соперники: {"kind": "winner"|"loser", "cands": [{name, acronym, image}]}."""
    m = re.match(r"(?i)^(.+?)\s+(winner|loser)$", (ph or "").strip())
    if not m or "/" not in m.group(1):
        return None
    cands = []
    for raw in m.group(1).split("/")[:2]:
        raw = raw.strip()
        c = idx.get(norm(raw)) or next((v for k, v in idx.items() if norm(raw) and (norm(raw) in k or k in norm(raw)) and min(len(k), len(norm(raw))) >= 3), None)
        cands.append(c or {"name": raw, "acronym": raw})
    return {"kind": m.group(2).lower(), "cands": cands}


def d2_sync(listing: list | None = None) -> None:
    """dust2.us: время начала матчей (как на HLTV) и вето перед матчем — как только команды его провели.
    1 запрос на список + по запросу на матч, который начинается в ближайшие 2 часа или уже идёт, пока вето нет."""
    lst = d2_upcoming()
    if not lst:
        return
    by_id = {str(x.get("id")): x for x in listing or []}
    names_idx = team_names_index(listing)
    n_t = n_v = 0
    for f in (OUT / "match").glob("*.json"):
        d = load_json(f, None)
        if not d or not d.get("teams") or (len(d["teams"]) != 2 and not d.get("tbd")):
            continue
        x = d2_find(lst, d["teams"], d.get("begin_at"))
        if not x:
            continue
        ch = False
        if d.get("tbd") and x.get("ph"):
            opp = tbd_opp(x["ph"][0], names_idx)
            if opp and opp != d.get("opp"):
                d["opp"] = opp; ch = True
                if str(d.get("id")) in by_id:
                    by_id[str(d.get("id"))]["opp"] = opp
        if x.get("begin") and x["begin"] != d.get("begin_at") and not d.get("veto_now"):
            d["begin_at"] = x["begin"]; ch = True; n_t += 1
            if str(d.get("id")) in by_id:
                by_id[str(d.get("id"))]["begin_at"] = x["begin"]
        bt = parse_iso(d.get("begin_at"))
        need = int(d.get("bo") or 3)
        have = len([s0 for s0 in (d.get("veto_now") or {}).get("steps") or [] if s0.get("act") in ("pick", "decider")])
        if bt and NOW - timedelta(hours=4) < bt < NOW + timedelta(hours=2) and have < need:
            h = d2_get(f"/matches/{x['id']}/{x['slug']}")
            steps = d2_parse_veto(re.sub(r"\s+", " ", h)) if h else []
            if steps:
                d["veto_now"] = {"steps": steps, "t": iso(NOW), "src": "dust2.us"}; ch = True; n_v += 1
        if ch:
            save_json(f, d)
    print(f"dust2: матчей в списке {len(lst)}, время поправлено {n_t}, вето перед матчем {n_v}")


def build_veto(teams: list[dict]) -> dict | None:
    """По каждой команде: что пикает, что банит первым и вообще, по последним матчам; плюс вероятный маппул."""
    res = []
    for t in teams:
        ht = hltv_team_for(t)
        if not ht:
            res.append(None)
            continue
        picks, bans, first_bans, n, deciders = {}, {}, {}, 0, {}
        wp, wb, wf, wn, vs = {}, {}, {}, 0.0, {}  # то же с весом свежести (полураспад 60 дней) и пики против конкретных соперников
        for mt in hltv_team_matches(ht)[:VETO_MATCHES]:
            v = hltv_veto(mt)
            if not v or not v.get("steps"):
                continue
            vd = parse_iso(v.get("date"))
            if vd and vd < NOW - timedelta(days=STATS_WINDOW_DAYS):
                continue  # только последние 3 месяца
            names = {norm(ht["name"]), norm(t.get("name")), norm(t.get("acronym"))} - {""}
            mine = [s for s in v["steps"] if s["team"] and (norm(s["team"]) in names
                                                             or any(len(x) >= 3 and (x in norm(s["team"]) or norm(s["team"]) in x) for x in names))]
            if not mine:
                continue
            n += 1
            w = 0.5 ** (max(0, (NOW - vd).days) / 60) if vd else 0.5
            wn += w
            opp = next((norm(s["team"]) for s in v["steps"] if s["team"] and s not in mine), "")
            for i, s in enumerate(mine):
                tgt = wp if s["act"] == "pick" else wb
                tgt[s["map"]] = tgt.get(s["map"], 0) + w
                if s["act"] == "ban" and i == 0:
                    wf[s["map"]] = wf.get(s["map"], 0) + w
                if s["act"] == "pick" and opp:
                    vs.setdefault(opp, {})[s["map"]] = vs.setdefault(opp, {}).get(s["map"], 0) + 1
            for s in v["steps"]:
                if s["act"] == "decider":
                    deciders[s["map"]] = deciders.get(s["map"], 0) + 1
            for i, s in enumerate(mine):
                tgt = picks if s["act"] == "pick" else bans
                tgt[s["map"]] = tgt.get(s["map"], 0) + 1
                if s["act"] == "ban" and i == 0:
                    first_bans[s["map"]] = first_bans.get(s["map"], 0) + 1
        top = lambda d: sorted(d.items(), key=lambda x: -x[1])
        rate = lambda d: {k: round(v / wn, 3) for k, v in d.items()} if wn else {}
        res.append({"team": ht["name"], "matches": n, "picks": top(picks), "bans": top(bans), "first_bans": top(first_bans),
                    "deciders": top(deciders), "pr": rate(wp), "br": rate(wb), "fr": rate(wf), "vs": vs})
    if not any(res):
        return None
    # вероятный маппул: что каждая пикает чаще всего и при этом соперник это не банит постоянно
    pool = []
    if all(res):
        for i, r in enumerate(res):
            opp = res[1 - i]
            ob = dict(opp["bans"])
            for mp, c in r["picks"]:
                if ob.get(mp, 0) < max(2, opp["matches"] // 2) and mp not in [p["map"] for p in pool]:
                    pool.append({"map": mp, "by": i, "picks": c})
                    break
        all_bans = {}
        for r in res:
            for mp, c in r["bans"]:
                all_bans[mp] = all_bans.get(mp, 0) + c
        picked = {p["map"] for p in pool}
        maps_seen = {mp for r in res for mp, _ in r["picks"] + r["bans"] + r.get("deciders", [])}
        decider = sorted((mp for mp in maps_seen if mp not in picked), key=lambda mp: all_bans.get(mp, 0))
        if decider:
            pool.append({"map": decider[0], "by": None})
    return {"teams": res, "likely_pool": pool}

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
    import html as _html
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = _html.unescape(s)
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
    r"\bCS ?2\b|\bCS:?GO\b|\bCS\b|Counter[- ]Strike|\bКС ?2?\b|контр-?страйк\w*|\bHLTV\b|\bIEM\b|\bBLAST\b|\bPGL\b|\bESL\b|\bmajor\b|мейджор"
    r"|\bAWP\b|\bIGL\b|капитан|снайпер|roster|состав|transfer|трансфер",
    re.IGNORECASE)
CS_SOURCES = {"hltv", "cybersport.ru", "dust2.us", "dust2.com.br", "bo3.gg", "esports.gg", "dexerto", "egamersworld",
              "escorenews", "cyber.sports.ru", "sports.ru", "championat", "offstage.ru", "vpesports", "esportsinsider",
              "hotspawn", "dust2", "gamespot", "pcgamer", "esports.net", "thespike", "gosugamers", "liquipedia"}
OTHER_GAMES = re.compile(
    r"\b(MLBB|Mobile Legends|Dota|Дот[аеуыо]\w*|The International|DreamLeague|Riyadh Masters|ESL One|Wallachia|BetBoom Dacha"
    r"|Valorant|Валорант\w*|League of Legends|\bLoL\b|PUBG|Fortnite|Apex|Rainbow Six|\bR6\b|Overwatch"
    r"|Rocket League|Call of Duty|\bCoD\b|Standoff|Стандофф\w*|Deadlock|Marvel Rivals|EA FC|FIFA|Free Fire|StarCraft"
    r"|Tekken|Hearthstone|World of Tanks|Мир танков|Warface|Brawl Stars|Clash|Fortnite|шахмат\w*|Chess"
    r"|Phantom Blade|трейлер\w*|trailer|PS5|PlayStation|Xbox|Nintendo|релиз игры|game release)\b",
    re.IGNORECASE)
CS_STRICT = re.compile(r"\bCS ?2\b|\bCS:?GO\b|\bCS\b|Counter[- ]Strike|\bКС ?2?\b|контр-?страйк\w*|\bHLTV\b|\bIEM\b"
                       r"|\bBLAST\b|мейджор\w*|\bAWP\b|авп\w*", re.IGNORECASE)
# Посты «во время матча»: моменты, ход игры, голый счёт — это трансляция, а не новость.
LIVE_NOISE = re.compile(
    r"клатч\w*|clutch|квадр\w*|\bквад\w*|\bэйс\w*|\bace\b|\b[345]k\b|\b[12] ?(?:в|v|vs|на) ?[2-5]\b|ситуаци\w+ \d ?(?:в|v|на) ?\d|хайлайт\w*|highlight|"
    r"\bмомент\w*|\bклип\w*|\bclip\b|прямо сейчас|\blive\b|\bлайв\b|идёт матч|идет матч|"
    r"(?:перв|втор|трет)\w+ карт\w*|\bкарт[ау] (?:за|в пользу)|забира\w+ (?:карту|mirage|inferno|nuke|ancient|anubis|dust2|train|overpass|vertigo|cache)|"
    r"взял\w* (?:карту|mirage|inferno|nuke|ancient|anubis|dust2|train|overpass)|счёт по картам|\bmap ?[123]\b|"
    r"\b\d{1,2} ?[:\-–] ?\d{1,2}\b.{0,20}(?:раунд|по раундам|на карте)|по раундам|пистолет\w* раунд\w*|раунд\w* за \w+|"
    r"в (?:одной )?карте от|десайдер\w*|решающ\w+ (?:карт\w*|ancient|mirage|inferno|nuke|anubis|dust2|train|overpass)|переводят на|сравнива\w+ сч|камбэк\w*|камбек\w*|камбечил\w*|\bэко\b|эко-раунд\w*|форс\w*|минус (?:три|четыре|пять)|\b(?:с|со) \d{1,2} ?[:\-–] ?\d{1,2}\b|сравнял\w* сч[её]т|приемк\w*|приёмк\w*",
    re.IGNORECASE)
RESULT_ONLY = re.compile(r"(?:обыгра\w+|победил\w*|выигра\w+|проигра\w+|уступил\w*|разгроми\w+|вылете\w+)\b.{0,60}\b[0-3] ?[:\-–] ?[0-3]\b",
                         re.IGNORECASE)
HAS_SUBSTANCE = re.compile(r"[«\"“].{8,}[»\"”]|интервью|заяв\w+|рассказ\w+|признал\w*|ответил\w*|высказал\w*|трансфер\w*|"
                           r"подписал\w*|покинул\w*|замен\w+|скамейк\w+|аренд\w+|контракт\w*|слух\w*|инсайд\w*|травм\w*",
                           re.IGNORECASE)


# превью и анонсы конкретного матча («MOUZ vs. FURIA — скоро узнаем…», «Сегодня Falcons против NAVI», опросы «как думаете»):
# актуальны только до матча — через 12 часов матч уже сыгран
PREVIEW = re.compile(
    r"\bvs\.?\s*$|^\W*карты матча|(?:\bvs\b|против|матч\w*).{0,80}(?:сегодня|скоро|узнаем|начина\w+|достойн\w+|будем смотреть|"
    r"прогноз\w*|предстоящ\w+)|(?:сегодня|скоро|узнаем|начина\w+|будем смотреть).{0,80}(?:\bvs\b|против)|как думаете|кто (?:будет сильнее|пройд\w+|победит)",
    re.IGNORECASE)


def is_stale_preview(title: str, date) -> bool:
    t = title or ""
    if re.search(r"[«\"“].{8,}[»\"”]|интервью", t, re.IGNORECASE):
        return False  # цитаты и интервью остаются — их можно рассказать и после матча
    return bool(date and NOW - date > timedelta(hours=12) and PREVIEW.search(t))


def is_live_noise(text: str) -> bool:
    t = (text or "")[:400]
    if HAS_SUBSTANCE.search(t):
        return False
    return bool(LIVE_NOISE.search(t) or RESULT_ONLY.search(t))


NON_CS_SOURCES = re.compile(r"dota|dota2\.ru|valorant|lol|mlbb", re.IGNORECASE)
SPAM = re.compile(
    r"prediction|predictions|прогноз|ставк|коэффициент|\bodds\b|betting|букмекер|live stream|how to watch|где смотреть"
    r"|смотреть онлайн|прямая трансляция|live score|в режиме live|результаты? (матча )?онлайн|счёт онлайн|счет онлайн"
    r"|расписани\w*|schedule|когда игра\w*|когда (пройд|начн|смотреть)\w*|анонс\w*|превью|preview|match ?up|head to head"
    r"|^Матч\b|^Match\b|^Live:|\bvs\.? .+ \d{1,2} (ноября|октября|декабря|november|october|december)"
    r"|сегодня игра\w*|today'?s matches|матчи дня|день \d+ ?[:—-]|day \d+ (schedule|preview)",
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


def distinctive_nick(nick: str) -> bool:
    """Ник, который не спутать с обычным словом: есть цифры или заглавные внутри (m0NESY, Boombl4, NiKo, XANTARES)."""
    return bool(re.search(r"\d", nick)) or bool(re.search(r"[a-z][A-Z]", nick)) or (nick.isupper() and len(nick) >= 5)


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


def translate_keep(text: str, keep: list[str]) -> tuple[str | None, str | None]:
    """Перевод, при котором ники и названия команд остаются как есть (Legacy не станет «Наследием»)."""
    if not text or not keep:
        return translate_ex(text)
    marks = {}
    t = text
    for i, name in enumerate(sorted({k for k in keep if k and len(k) >= 2}, key=len, reverse=True)):
        rx = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.IGNORECASE)
        if rx.search(t):
            mark = f"ZQ{i}X"
            marks[mark] = name
            t = rx.sub(mark, t)
    ru, lang = translate_ex(t)
    if ru:
        for mark, name in marks.items():
            ru = re.sub(re.escape(mark), name, ru, flags=re.IGNORECASE)
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
DEDUP_CACHE_F = CACHE / "news_dedup.json"
dedup_cache: dict = load_json(DEDUP_CACHE_F, {})
art_cache: dict = load_json(ART_CACHE_F, {})
ART_NEW_LIMIT = 40           # сколько новых ссылок Google News раскрывать за запуск (Google ограничивает частоту)
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
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _art_new[0] = ART_NEW_LIMIT  # Google просит притормозить — остальное в следующий запуск
            warn("Google News: слишком часто, остальные ссылки раскроются в следующий запуск")
        else:
            warn(f"Google News ссылка: {e!r}")
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


# ---------------------------------------------------------------- GigaChat

GIGA_KEY = os.environ.get("GIGACHAT_KEY", "").strip()
GIGA_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2")
GIGA_CA_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
_giga = {"token": None, "exp": 0.0, "ctx": None, "off": False}

# Дневные лимиты токенов, чтобы бесплатного пакета хватило на год:
# базовая модель 250 млн/год → 500 тыс./день, Max (картинки) 25 млн/год → 50 тыс./день.
GIGA_DAY_LIMIT = {"lite": int(os.environ.get("GIGA_DAY_LITE", "500000")), "max": int(os.environ.get("GIGA_DAY_MAX", "10000000")),  # Max — только имена; без дневной планки (05.11, решение владельца), ограничивает лишь пакет Сбера
                  "pro": int(os.environ.get("GIGA_DAY_PRO", "100000"))}
GIGA_USAGE_F = CACHE / "giga_usage.json"
giga_usage: dict = load_json(GIGA_USAGE_F, {})


def giga_budget_ok(kind: str) -> bool:
    day = NOW.strftime("%Y-%m-%d")
    used = (giga_usage.get(day) or {}).get(kind, 0)
    return used < GIGA_DAY_LIMIT[kind]


def giga_spend(kind: str, tokens: int) -> None:
    day = NOW.strftime("%Y-%m-%d")
    d = giga_usage.setdefault(day, {})
    d[kind] = d.get(kind, 0) + int(tokens or 0)


def giga_ctx():
    if _giga["ctx"] is None:
        import ssl
        ctx = ssl.create_default_context()
        ca_file = CACHE / "russian_trusted_root_ca.pem"
        try:
            if not ca_file.exists():
                ca_file.write_bytes(http_get(GIGA_CA_URL, timeout=20))
            ctx.load_verify_locations(cadata=ca_file.read_text())
        except Exception as e:
            warn(f"GigaChat сертификат: {e!r}")
        _giga["ctx"] = ctx
    return _giga["ctx"]


def giga_token() -> str | None:
    if _giga["token"] and time.time() < _giga["exp"] - 60:
        return _giga["token"]
    import uuid
    req = urllib.request.Request("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", data=b"scope=GIGACHAT_API_PERS",
                                 method="POST", headers={"Authorization": f"Basic {GIGA_KEY}", "RqUID": str(uuid.uuid4()),
                                                         "Content-Type": "application/x-www-form-urlencoded",
                                                         "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=giga_ctx()) as r:
        j = json.loads(r.read())
    _giga["token"] = j["access_token"]
    _giga["exp"] = (j.get("expires_at") or 0) / 1000 or time.time() + 1500
    return _giga["token"]


def giga_kind(model: str | None) -> str:
    m = (model or GIGA_MODEL).lower()
    return "max" if "max" in m else "pro" if "pro" in m else "lite"


FAST = os.environ.get("NEWS_ONLY") == "1"   # быстрое обновление: только новости из Telegram


# ---------------------------------------------------------------- Gemini (основная нейросеть) и GigaChat (запасная)

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_DAY_LIMIT = int(os.environ.get("GEMINI_DAY_LIMIT", "1500"))   # запросов в день — запас до бесплатного лимита Google
GEMINI_LITE = os.environ.get("GEMINI_LITE", "gemini-flash-lite-latest")  # запасная облегчённая модель: обычно свободна, тоже бесплатна
_gem = {"off": False, "last": 0.0, "fails": 0, "model": GEMINI_MODEL, "think": True, "busy": 0}


def gem_ok() -> bool:
    if not GEMINI_KEY or _gem["off"]:
        return False
    return (giga_usage.get(NOW.strftime("%Y-%m-%d")) or {}).get("gemini", 0) < GEMINI_DAY_LIMIT


def llm_ok() -> bool:
    return gem_ok() or bool(GIGA_KEY and not _giga["off"])


def gemini_call(system: str | None, parts: list, max_tokens: int = 900, json_mode: bool = True) -> str | None:
    if not gem_ok():
        return None
    for attempt in range(5):
        wait = 4.5 - (time.time() - _gem["last"])  # бесплатный уровень: ~15 запросов в минуту
        if wait > 0:
            time.sleep(wait)
        _gem["last"] = time.time()
        cfg = {"temperature": 0.3, "maxOutputTokens": max(1024, max_tokens * 2)}
        if json_mode:
            cfg["responseMimeType"] = "application/json"
        if _gem["think"]:
            cfg["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_gem['model']}:generateContent"
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read())
            day = NOW.strftime("%Y-%m-%d")
            giga_usage.setdefault(day, {})["gemini"] = (giga_usage.get(day) or {}).get("gemini", 0) + 1
            stats["gemini_calls"] = stats.get("gemini_calls", 0) + 1
            _gem["fails"] = _gem["busy"] = 0
            cand = (j.get("candidates") or [{}])[0]
            return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []) or None
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "replace")
            if e.code == 400 and _gem["think"]:  # облегчённые модели не принимают thinkingConfig («invalid argument»)
                _gem["think"] = False
                continue
            if e.code == 404 and _gem["model"] == GEMINI_LITE and GEMINI_LITE != "gemini-3.1-flash-lite":
                _gem["model"] = "gemini-3.1-flash-lite"
                continue
            if e.code == 404 and _gem["model"] not in ("gemini-2.5-flash", "gemini-3.1-flash-lite"):
                _gem["model"] = "gemini-2.5-flash"
                continue
            if e.code in (500, 502, 503, 504):  # модель перегружена: подождать, потом перейти на облегчённую
                if attempt == 0:
                    time.sleep(8)
                    continue
                if _gem["model"] not in (GEMINI_LITE, "gemini-3.1-flash-lite"):
                    warn(f"Gemini {_gem['model']} перегружена — переключаюсь на {GEMINI_LITE}")
                    _gem["model"] = GEMINI_LITE
                    continue
                if attempt < 4:
                    time.sleep(15)
                    continue
                _gem["busy"] += 1
                if _gem["busy"] >= 4:
                    _gem["off"] = True  # перегружены обе — до конца прогона работает GigaChat
            if e.code == 429 and attempt == 0:
                time.sleep(25)
                continue
            if e.code == 429 and _gem["model"] not in (GEMINI_LITE, "gemini-3.1-flash-lite"):
                _gem["model"] = GEMINI_LITE  # у облегчённой модели свой дневной лимит
                continue
            if e.code in (401, 403, 429):
                _gem["off"] = True  # ключ не подходит или кончился дневной лимит — до конца прогона работает GigaChat
            warn(f"Gemini: HTTP {e.code} {msg[:120]}")
            return None
        except Exception as e:
            _gem["fails"] += 1
            if _gem["fails"] >= 3:
                _gem["off"] = True
            warn(f"Gemini: {e!r}")
            return None
    return None


def giga_chat(system: str, user: str, max_tokens: int = 900, model: str | None = None) -> str | None:
    if FAST and system is DEDUP_SYSTEM:
        return None  # в быстром режиме повторы в новостях матча склеиваем без нейросети (экономим лимит)
    if gem_ok():
        txt = gemini_call(system, [{"text": user}], max_tokens)
        if txt:
            return txt
    return gigachat_call(system, user, max_tokens, model)


def gigachat_call(system: str, user: str, max_tokens: int = 900, model: str | None = None) -> str | None:
    if not GIGA_KEY or _giga["off"]:
        return None
    if not giga_budget_ok(giga_kind(model)):
        if not _giga.get("warned_lite"):
            warn("GigaChat: дневной лимит токенов исчерпан, остальное — завтра")
            _giga["warned_lite"] = True
        return None
    body = json.dumps({"model": model or GIGA_MODEL, "temperature": 0.3, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://gigachat.devices.sberbank.ru/api/v1/chat/completions", data=body,
                                         method="POST", headers={"Authorization": f"Bearer {giga_token()}",
                                                                 "Content-Type": "application/json", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90, context=giga_ctx()) as r:
                j = json.loads(r.read())
            stats["giga_calls"] = stats.get("giga_calls", 0) + 1
            stats["giga_tokens"] = stats.get("giga_tokens", 0) + (j.get("usage") or {}).get("total_tokens", 0)
            giga_spend(giga_kind(model), (j.get("usage") or {}).get("total_tokens", 0))
            _giga["fails"] = 0
            return j["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            msg = e.read()[:200].decode("utf-8", "replace")
            if e.code == 401:
                _giga["token"] = None
                continue
            if e.code == 429 and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            if e.code == 402:
                _giga["off"] = True  # закончился бесплатный пакет
            warn(f"GigaChat: HTTP {e.code} {msg}")
            return None
        except Exception as e:
            warn(f"GigaChat: {e!r}")
            if attempt < 2:
                time.sleep(3)
                continue
            _giga["fails"] = _giga.get("fails", 0) + 1
            if _giga["fails"] >= 3:
                _giga["off"] = True  # сервис недоступен — не тратим время до следующего прогона
            return None
    return None


def parse_json_loose(txt: str | None) -> dict | None:
    if not txt:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.MULTILINE).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


# ---------------------------------------------------------------- статья: текст и пересказ

ART_TEXT_LIMIT = 7000        # сколько символов статьи отдаём нейросети
ART_SUMMARY_LIMIT = int(os.environ.get("ART_SUMMARY_LIMIT", "45"))  # новых пересказов за запуск
_art_sum = [0]


def article_text(url: str) -> tuple[str | None, str | None]:
    """Основной текст статьи и картинка."""
    try:
        html = http_get(url, {"Accept": "text/html,application/xhtml+xml"}, timeout=25)[:1_500_000].decode("utf-8", "replace")
    except Exception:
        return None, None
    image = _meta(html, "og:image", "twitter:image")
    text = None
    try:
        import trafilatura  # ставится в GitHub Actions
        text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True)
    except Exception:
        text = None
    if not text:
        paras = [strip_html(m.group(1)) for m in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)]
        text = "\n".join(p for p in paras if len(p) > 60)
    return (text.strip() or None) if text else None, image


SUM_SYSTEM = """Ты редактор русскоязычного киберспортивного издания про Counter-Strike 2.
Тебе дают статью (на любом языке). Перескажи её по-русски СВОИМИ СЛОВАМИ для комментатора, который готовится к матчу.
Правила:
- Не переводи текст подряд и не копируй предложения: только пересказ сути, 4–8 предложений, нейтрально.
- Сохрани важные факты: кто, что сказал или сделал, цифры, турниры, составы.
- Ники игроков и названия команд НИКОГДА не переводи и не склоняй по смыслу: Legacy, Heroic, Liquid, Falcons, Spirit, Aurora, Vitality,
  MOUZ и т.д. остаются латиницей как в оригинале — это названия, а не слова. То же в заголовке title_ru.
- Можно привести не больше 2 коротких цитат (каждая до 15 слов) — самые яркие высказывания, переведённые на русский.
- Ничего не выдумывай. Если в тексте нет статьи (ошибка, реклама, пусто) — верни "ok": false.
Ответ строго в JSON без пояснений:
{"ok": true, "lang": "<код языка оригинала: ru/en/pt/...>", "title_ru": "<заголовок по-русски>",
 "summary_ru": "<пересказ>", "quotes": [{"ru": "<цитата по-русски>", "who": "<кто сказал>"}]}"""


def summarize_article(it: dict, url: str) -> dict:
    text, image = article_text(url)
    info: dict = {"image": image}
    if not text or len(text) < 200:
        return info
    text = text[:ART_TEXT_LIMIT]
    _art_sum[0] += 1
    res = parse_json_loose(giga_chat(SUM_SYSTEM, f"Источник: {it['source']}\nЗаголовок: {it['title']}\n\nТекст статьи:\n{text}"))
    if res and res.get("ok") and res.get("summary_ru"):
        quotes = [q for q in (res.get("quotes") or []) if isinstance(q, dict) and q.get("ru") and len(q["ru"].split()) <= 20][:2]
        info.update({"title_ru": res.get("title_ru"), "summary_ru": res["summary_ru"].strip(), "quotes": quotes,
                     "lang": (res.get("lang") or "").lower()[:5] or None})
    return info


def article_info(it: dict) -> dict:
    """Ссылка на оригинал, пересказ, цитаты. Всё кэшируется по новости."""
    if it.get("tg"):
        return {"url": it["url"], "summary_ru": tg_text_preview(it.get("text") or "", 100000), "image": it.get("photo"),
                "photos": it.get("photos") or [], "video": it.get("video"), "lang": "ru"}
    k = it["key"]
    info = dict(art_cache.get(k) or {})
    url = info.get("url") or (it["url"] if "news.google.com" not in (it["url"] or "") else None)
    if not url and _art_new[0] < ART_NEW_LIMIT:
        _art_new[0] += 1
        url = gnews_decode(it["url"])
        time.sleep(2)
    if url:
        info["url"] = url
    tried = info.get("sum_tried")
    if url and GIGA_KEY and not tried and not _giga["off"] and _art_sum[0] < ART_SUMMARY_LIMIT:
        info.update(summarize_article(it, url))
        info["sum_tried"] = True
    if info:
        info["t"] = info.get("t") or iso(NOW)
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

    # Источник новостей — только новостные Telegram-каналы (TG_NEWS_CHANNELS).
    # Сайты (Google News, RSS) отключены по просьбе пользователя: USE_WEB_NEWS=1 вернёт их.
    pool: list[dict] = list(tg_posts())
    if os.environ.get("USE_WEB_NEWS") == "1":
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
        if (OTHER_GAMES.search(text) or NON_CS_SOURCES.search(it["source"] or "")) and not CS_STRICT.search(title):
            continue
        src = (it["source"] or "").lower()
        cs_src = any(s in src for s in CS_SOURCES) or bool(it.get("tg"))
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
        nick_only = [a for a in about if a not in team_names]
        if nick_only and len(nick_only) == len(about) and all(not distinctive_nick(a) for a in nick_only):
            # простой ник (Zero, Jee, Mercury): нужна команда, партнёр или явный CS рядом
            team_of = {e[0]: teams[e[3]] for e in entities}
            ctx_ok = any(x in src for x in ("hltv", "dust2")) or bool(it.get("tg"))
            for a in nick_only:
                t = team_of.get(a) or {}
                mates = [p.get("nick") for p in t.get("players") or [] if p.get("nick") and p.get("nick") != a]
                if any(entity_re(x, False).search(text) for x in [t.get("name"), t.get("acronym")] + mates if x and len(x) >= 2):
                    ctx_ok = True
            if not ctx_ok:
                continue
        only_teams = all(a in team_names for a in about)
        cs_only_src = any(x in src for x in ("hltv", "dust2")) or bool(it.get("tg"))
        if only_teams and not (CS_STRICT.search(text) or cs_only_src):
            continue
        if not (cs_src or CS_CONTEXT.search(text) or not only_teams):
            continue
        k = item_key(it)
        cur = found.get(k)
        if cur:
            for a in about:
                if a not in cur["about"]:
                    cur["about"].append(a)
            continue
        found[k] = {**it, "about": about, "key": k}

    items = sorted(found.values(), key=lambda i: i["date"], reverse=True)[:90]
    keep = [x for t in teams for x in [t.get("name"), t.get("acronym")] + [p.get("nick") for p in t.get("players") or []]]
    keep += ["IEM", "BLAST", "PGL", "ESL", "HLTV", "Major", "VRS", "CS2", "Counter-Strike", "Vitality", "Spirit", "MOUZ",
             "Falcons", "Liquid", "Heroic", "Legacy", "Aurora", "Monte", "Astralis", "Complexity", "Nemiga", "Virtus.pro"]
    for it in items:
        t_ru, t_lang = translate_keep(it["title"], keep)
        it["title_ru0"], it["lang0"] = (t_ru if t_lang != "ru" else None), t_lang
    clusters = dedupe_strict(items)  # только повторы одной новости, главная — самая подробная

    out = []
    for cl in clusters[:60]:
        it, others = cl[0], cl[1:]
        art = article_info(it)
        lang = art.get("lang") or it["lang0"]
        t_ru = it["title_ru0"] if lang != "ru" else None
        about = []
        for x in cl:
            for a in x["about"]:
                if a not in about:
                    about.append(a)
        out.append({
            "key": it["key"],
            "title": it["title"],
            "title_ru": t_ru,
            "lang": lang,
            "lang_ru": LANG_RU.get(lang or "", lang) if lang and lang != "ru" else None,
            "summary_ru": art.get("summary_ru"),
            "quotes": art.get("quotes") or [],
            "image": art.get("image"), "photos": art.get("photos") or [], "video": art.get("video"),
            "cat": it.get("cat"), "tour": it.get("tour"),
            "kind": classify(it["title"]),
            "about": about,
            "source": it["source"],
            "url": art.get("url") or it["url"],
            "date": iso(it["date"]),
            "also": [{"source": o["source"], "url": (art_cache.get(o["key"]) or {}).get("url") or o["url"],
                      "title": o.get("title_ru0") or o["title"]} for o in others],
        })
    return out


STOP = set("""и в во на с со по о об от до за из к ко у не что это как а но или же ли бы для при про над под после перед
the a an of to in on at for and or but with from by is are was were be as his her their its he she they it this that
после против vs""".split())
SRC_RANK = ["cybersport.ru", "hltv", "sports.ru", "championat", "dust2", "bo3.gg", "escorenews", "esports.gg", "dexerto"]


def title_tokens(t: str) -> set:
    words = re.findall(r"[a-zа-яё0-9]+", (t or "").lower())
    return {w[:6] for w in words if len(w) >= 3 and w not in STOP}  # первые 6 букв — грубый учёт склонений


def src_rank(it: dict) -> tuple:
    s = (it.get("source") or "").lower()
    r = next((i for i, x in enumerate(SRC_RANK) if x in s), len(SRC_RANK))
    return (r, 0 if it.get("lang0") == "ru" else 1, -it["date"].timestamp())  # в группе главным — самый свежий пост


DEDUP_SYSTEM = """Ты редактор новостей о Counter-Strike 2. Тебе дают пронумерованный список заголовков (уже по-русски) и составы двух команд.
1) В "drop" перечисли номера заголовков, которые НЕ про киберспорт Counter-Strike и не про этих игроков/команды
   (другие игры, кино, музыка, однофамильцы, составы клубов в Dota/Valorant и т.п.), А ТАКЖЕ расписания, анонсы и превью матчей,
   страницы «матч X — Y, дата и время», «где смотреть», онлайн-результаты и прогнозы на ставки — это не новости.
2) В "groups" перечисли группы номеров, которые рассказывают ОДНУ И ТУ ЖЕ новость (одно событие), даже если формулировки и языки разные.
   Разные события про одного игрока — это разные новости, их не объединяй.
3) В "stale" перечисли номера устаревших новостей: более ранняя новость, которую обогнала более поздняя про ту же историю
   (было «в шаге от MVP» / «может перейти» / «идёт на рекорд» — потом вышло «получил MVP» / «перешёл» / «побил рекорд»).
   Смотри на время в квадратных скобках: устаревшей может быть только более ранняя.
Ответ строго JSON: {"drop": [номера], "groups": [[номера], [номера]], "stale": [номера]}"""


def stale_ids(res: dict, dates: list) -> set:
    """Устаревшие новости (их обогнало продолжение той же истории). Самую свежую новость не трогаем."""
    out = set()
    newest = max(dates) if dates else None
    for x in res.get("stale") or []:
        if str(x).isdigit() and 0 < int(x) <= len(dates) and dates[int(x) - 1] != newest:
            out.add(int(x) - 1)
    return out


def dedupe_news(items: list[dict], teams: list[dict], same_who_required: bool = True) -> list[list[dict]]:
    """Склеивает одну и ту же новость с разных сайтов. Первый в группе — основной."""
    clusters: list[list[dict]] = []
    for it in items:
        tok = title_tokens(it["title_ru0"] or it["title"])
        it["_tok"] = tok
        best, best_sim = None, 0.0
        for cl in clusters:
            for o in cl:
                inter = len(tok & o["_tok"])
                union = len(tok | o["_tok"]) or 1
                sim = inter / union
                close = abs((it["date"] - o["date"]).total_seconds()) < 36 * 3600
                same_who = bool(set(it["about"]) & set(o["about"])) or not same_who_required
                if close and same_who and (sim >= 0.45 or (sim >= 0.3 and inter >= 3)) and sim > best_sim:
                    best, best_sim = cl, sim
        if best is not None:
            best.append(it)
        else:
            clusters.append([it])

    # второй проход: GigaChat — посторонние новости и дубли, которые не поймали правила
    if llm_ok() and len(clusters) > 1:
        roster = "\n".join(f"{t['name']}: " + ", ".join(p.get("nick") or "" for p in t.get("players") or []) for t in teams) or "(общая лента новостей CS2 — оценивай только про CS2 или нет)"
        lines = "\n".join(f"{i + 1}. [{max(x['date'] for x in cl).strftime('%d.%m %H:%M')}; {', '.join(cl[0]['about'])}] {cl[0]['title_ru0'] or cl[0]['title']}"
                          for i, cl in enumerate(clusters))
        sig = hashlib.sha1((roster + lines).encode()).hexdigest()[:16]
        cached = dedup_cache.get(sig)
        res = cached["res"] if cached else parse_json_loose(giga_chat(DEDUP_SYSTEM, f"Составы:\n{roster}\n\nЗаголовки:\n{lines}", 900))
        if res is not None:
            dedup_cache[sig] = {"res": res, "t": iso(NOW)}
            drop = {int(x) - 1 for x in res.get("drop") or [] if str(x).isdigit()}
            drop |= stale_ids(res, [max(x["date"] for x in cl) for cl in clusters])
            merged = []
            used = set()
            for g in res.get("groups") or []:
                idx = sorted({int(x) - 1 for x in g if str(x).isdigit() and 0 < int(x) <= len(clusters)} - drop - used)
                if len(idx) > 1:
                    merged.append(sum((clusters[i] for i in idx), []))
                    used |= set(idx)
            for i, cl in enumerate(clusters):
                if i not in used and i not in drop:
                    merged.append(cl)
            clusters = merged

    for cl in clusters:
        cl.sort(key=src_rank)
    clusters.sort(key=lambda cl: max(x["date"] for x in cl), reverse=True)
    return clusters


def build_news(teams: list[dict]) -> dict:
    return {"updated": iso(NOW), "days": NEWS_DAYS, "items": gather_news(teams)}



# ---------------------------------------------------------------- факты для эфира

FACTS_CACHE_F = CACHE / "facts_v11.json"
facts_cache: dict = load_json(FACTS_CACHE_F, {})
FACTS_TTL_H = 6
FACTS_SYSTEM = """Ты помогаешь русскоязычному комментатору CS2 подготовиться к матчу.
Тебе дают новости недели о командах и игроках матча (из Telegram-каналов) и список ников.
Выбери из этих новостей до 10 САМЫХ интересных и необычных фактов для эфира — то, что зритель вряд ли знает и что стоит сказать вслух.
Обычные новости (результат матча, «команда готовится», рядовая статистика, анонсы) пропускай. Лучше 3 сильных факта, чем 10 проходных.
Факты бери ТОЛЬКО из этих новостей: цитаты и заявления, интервью, трансферы и замены, слухи, конфликты,
травмы, достижения и интересные особенности из новостей. Каждая важная новость — отдельный факт.
Строгие правила:
- Только то, что прямо написано в новостях. Ничего не добавляй от себя.
- НЕ пиши общих сведений о составе (кто капитан, кто AWP, кто в аренде, возраст, страна) и о прошлых встречах команд —
  это и так видно в приложении. Такое можно только если это само по себе новость недели (например, «X стал капитаном»).
- Числа (счёт, места, даты) переписывай точно как в новостях, не считай сам. Смысл не меняй: пиши близко к тексту новости.
- Кто сказал или сделал — строго как в новости; не приписывай слова другому человеку.
- Итоги матчей и турниров (cat = result) бери, только если это текущий турнир матча (match.tournament).
  О прошлых турнирах — только рекорды, антирекорды, цитаты и мнения.
- Если новостей нет — верни пустой список, не выдумывай.
- Одно предложение до 25 слов, по-русски, ники и названия команд латиницей как в данных.
- "tag": одно из — Новости, Цитата, Интервью, Трансфер, Слухи, Конфликт, Достижения, Особенности (необычные детали: привычки, здоровье, забавные факты).
- "src": номер новости ("n"), из которой взят факт.
Ответ строго JSON: {"facts": [{"tag": "...", "text": "...", "src": 1}]}"""


def facts_input(d: dict) -> dict:
    A, B = (d.get("teams") or [{}, {}])[:2]
    def team(t):
        return {
            "name": t.get("name"),
            "coach": [{"nick": c.get("nick"), "age": c.get("age")} for c in t.get("coach") or []],
            "players": [{"nick": p.get("nick"), "name": p.get("name_ru") or " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
                         "age": p.get("age"), "country": p.get("nationality"), "roles": p.get("tags")} for p in t.get("players") or []],
            "player_stats_3m": [{k: x.get(k) for k in ("nick", "kd", "adr", "kast", "best_map", "maps")} for x in t.get("stats") or []],
            "last10": [{"vs": f.get("opponent_name"), "score": f"{f.get('score')}:{f.get('opp_score')}", "won": f.get("won"),
                        "tournament": f.get("tournament"), "date": (f.get("date") or "")[:10]} for f in t.get("form") or []],
        }
    maps = d.get("maps") or {}
    sides = d.get("sides") or {}
    return {
        "match": {"tournament": d.get("tournament"), "stage": d.get("stage"), "bo": d.get("bo"), "date": (d.get("begin_at") or "")[:10]},
        "team1": team(A), "team2": team(B),
        "h2h_year": [{"date": (h.get("date") or "")[:10], "tournament": h.get("tournament"), "score": h.get("score"),
                      "maps": [f"{g.get('map')} {g.get('score') or ''} ({['team1', 'team2'][g['winner']] if g.get('winner') in (0, 1) else '?'})"
                               for g in h.get("maps") or []]} for h in d.get("h2h") or []],
        "map_winrates_4m": [{"map": r["map"], "team1": f"{r['teams'][0]['won']}/{r['teams'][0]['played']}",
                             "team2": f"{r['teams'][1]['won']}/{r['teams'][1]['played']}"} for r in maps.get("table") or []],
        "sides_3m_total": {"team1": (sides.get("total") or [None, None])[0], "team2": (sides.get("total") or [None, None])[1]},
        "sides_3m_by_map": sides.get("maps"),
        "veto_last_matches": d.get("veto"),
        "news_week": [{"n": i + 1, "about": n.get("about"), "cat": n.get("cat"), "tour": n.get("tour"),
                       "title": n.get("title_ru") or n.get("title"),
                       "text": (n.get("summary_ru") or "")[:300]} for i, n in enumerate(((d.get("news") or {}).get("items") or [])[:15])],
    }


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)); d, h = n % 10, n % 100
    return one if d == 1 and h != 11 else few if 2 <= d <= 4 and not 12 <= h <= 14 else many


COUNTRY_RU = {"UA": "Украина", "RU": "Россия", "BY": "Беларусь", "KZ": "Казахстан", "BR": "Бразилия", "AR": "Аргентина", "CL": "Чили",
              "UY": "Уругвай", "DK": "Дания", "SE": "Швеция", "FI": "Финляндия", "NO": "Норвегия", "FR": "Франция", "DE": "Германия",
              "PL": "Польша", "MN": "Монголия", "CN": "Китай", "US": "США", "CA": "Канада", "TR": "Турция", "BA": "Босния",
              "RS": "Сербия", "LV": "Латвия", "LT": "Литва", "EE": "Эстония", "IL": "Израиль", "HU": "Венгрия", "CZ": "Чехия",
              "SK": "Словакия", "PT": "Португалия", "ES": "Испания", "GB": "Великобритания", "AU": "Австралия"}


def code_facts(d: dict) -> list[dict]:
    """Факты с цифрами — считает код (всегда точно). Только яркие: обычную статистику не добавляем."""
    out: list[dict] = []
    teams = (d.get("teams") or [])[:2]
    if len(teams) < 2:
        return out
    A, B = teams
    nm = [A.get("name"), B.get("name")]
    # последняя личная встреча — как сюжет; доминирование — если одна команда выиграла все (3+)
    h2h = d.get("h2h") or []
    if h2h:
        wa = sum(1 for h in h2h if h.get("winner") == 0); wb = sum(1 for h in h2h if h.get("winner") == 1)
        if len(h2h) >= 3 and (wa == 0 or wb == 0):
            dom = nm[0] if wb == 0 else nm[1]
            out.append({"tag": "Встречи", "text": f"{dom} выиграли все {len(h2h)} личные встречи за последний год."})
    # длинные серии
    for t in teams:
        f = t.get("form") or []
        if not f:
            continue
        streak, kind = 0, f[0].get("won")
        for x in f:
            if x.get("won") == kind:
                streak += 1
            else:
                break
        if streak >= 5:
            out.append({"tag": "Форма", "text": f"{t['name']} идут на серии из {streak} {'побед' if kind else 'поражений'} подряд."})
    # люди: аренды, весь состав из одной страны, экстремальный возраст
    for t in teams:
        cs = [p.get("nationality") for p in t.get("players") or [] if p.get("nationality")]
        if cs and len(set(cs)) == 1 and len(cs) >= 5:
            out.append({"tag": "Игроки", "text": f"У {t['name']} весь состав из одной страны — {COUNTRY_RU.get(cs[0], cs[0])}."})
    people = [(p, t["name"]) for t in teams for p in t.get("players") or [] if p.get("age")]
    if people:
        y = min(people, key=lambda x: x[0]["age"]); o = max(people, key=lambda x: x[0]["age"])
        if y[0]["age"] <= 17:
            out.append({"tag": "Игроки", "text": f"{y[0]['nick']} ({y[1]}) — самый молодой в матче: {y[0]['age']} {plural(y[0]['age'], 'год', 'года', 'лет')}."})
        if o[0]["age"] >= 33:
            out.append({"tag": "Игроки", "text": f"{o[0]['nick']} ({o[1]}) — самый опытный в матче: {o[0]['age']} {plural(o[0]['age'], 'год', 'года', 'лет')}."})
    # яркая статистика
    tot = (d.get("sides") or {}).get("total") or [None, None]
    for i in (0, 1):
        x = tot[i]
        if not x:
            continue
        pn = x.get("pistol_n") or [0, 0]
        if x.get("pistol") is not None and pn[1] >= 30 and (x["pistol"] >= 60 or x["pistol"] <= 40):
            out.append({"tag": "Стороны", "text": f"{nm[i]} выигрывают {x['pistol']}% пистолеток за 3 месяца ({pn[0]} из {pn[1]})."})
        ct, tt = x.get("ct"), x.get("t")
        if ct is not None and tt is not None and abs(ct - tt) >= 10:
            out.append({"tag": "Стороны", "text": f"У {nm[i]} сильный перекос в {'защиту' if ct > tt else 'атаку'}: "
                        f"{ct}% раундов за CT против {tt}% за T."})
    for r in (d.get("maps") or {}).get("table") or []:
        for i in (0, 1):
            w, pl = r["teams"][i]["won"], r["teams"][i]["played"]
            if pl >= 8 and w / pl >= 0.8:
                out.append({"tag": "Карты", "text": f"{nm[i]} на {r['map']}: {w} побед из {pl} за 4 месяца."})
    return out


def _numbers(text: str) -> set:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


# Слова, за которыми обычно прячется выдуманная «статистика»: числительные, серии, титулы, рекорды.
RISKY_WORDS = re.compile(
    r"\b(одн[аоиу]\w*|два|две|двух|тр[иёе]\w*|четыр\w*|пят\w*|шест\w*|сем[ьи]\w*|восьм\w*|девят\w*|десят\w*|"
    r"дважды|трижды|впервые|никогда|подряд|серия|серию|титул\w*|чемпион\w*|трофе\w*|рекорд\w*|кубок\w*|"
    r"побед\w* на турнир\w*|выиграл\w* турнир\w*|лучш\w* в истории)\b", re.IGNORECASE)


def _risky_ok(text: str, source: str) -> bool:
    """Каждое «рискованное» слово из факта должно встречаться в исходных данных (с точностью до окончания)."""
    src = source.lower()
    for m in RISKY_WORDS.finditer(text):
        stem = m.group(0).lower()[:5]
        if stem not in src:
            return False
    return True


def fact_source(f: dict, news_items: list) -> str | None:
    """Ключ новости, из которой взят факт: по номеру от модели, иначе по совпадению слов."""
    try:
        i = int(f.get("src")) - 1
        if 0 <= i < len(news_items):
            return news_items[i].get("key")
    except (TypeError, ValueError):
        pass
    tok = title_tokens(f.get("text"))
    best, score = None, 0.0
    for n in news_items:
        o = title_tokens((n.get("title_ru") or n.get("title") or "") + " " + (n.get("summary_ru") or "")[:200])
        sc = len(tok & o) / max(1, len(tok))
        if sc > score:
            best, score = n.get("key"), sc
    return best if score >= 0.4 else None


def build_facts(d: dict, prev: dict | None) -> list | None:
    key = str(d["id"])
    stats_facts = code_facts(d)
    c = facts_cache.get(key)
    story = None
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=FACTS_TTL_H):
        story = c["story"]
    elif llm_ok():
        inp = facts_input(d)
        if not inp.get("news_week"):
            inp = None
        else:
            inp = {"match": inp.get("match"), "news_week": inp["news_week"],
                   "teams": [{"name": inp[k]["name"], "nicks": [p["nick"] for p in inp[k].get("players") or []]}
                             for k in ("team1", "team2") if k in inp]}
        data = json.dumps(inp, ensure_ascii=False) if inp else ""
        res = parse_json_loose(giga_chat(FACTS_SYSTEM, data[:14000], 1800, model=os.environ.get("GIGACHAT_FACTS_MODEL", "GigaChat-2-Pro"))) if inp else {"facts": []}
        allowed = _numbers(data)
        news_items = ((d.get("news") or {}).get("items") or [])[:15]
        failed = inp is not None and res is None
        story = []
        for f in (res or {}).get("facts") or []:
            if not isinstance(f, dict) or not f.get("text"):
                continue
            if not _numbers(f["text"]) <= allowed:
                continue  # число, которого нет в данных, — модель что-то посчитала или придумала
            if not _risky_ok(f["text"], data):
                continue  # «три турнира подряд», «чемпион», «впервые» — только если это прямо есть в данных
            if re.search(r"нет новост|не поступал|не было новост|^\s*нет\b|не найден|отсутству", f["text"], re.IGNORECASE):
                continue
            if re.search(r"капитан\w* (команды|является)|является капитан|на позиции AWP|позицию AWP|играет на AWP|в аренде из|"
                         r"последн\w+ (личн\w+ )?встреч", f["text"], re.IGNORECASE) and not re.search(r"стал|назнач|перешёл|перешел|подписал", f["text"], re.IGNORECASE):
                continue  # это видно в профилях и в блоке встреч
            if LIVE_NOISE.search(f["text"]) and not HAS_SUBSTANCE.search(f["text"]):
                continue  # ход матча (камбэки, эко, счёт по раундам) — не факт для эфира
            tag = f.get("tag") if f.get("tag") in ("Новости", "Цитата", "Интервью", "Трансфер", "Слухи", "Конфликт", "Достижения", "Особенности") else "Новости"
            story.append({"tag": tag, "text": f["text"].strip(), "src": fact_source(f, news_items)})
        story = story[:10]
        if failed:
            story = (c or {}).get("story")  # GigaChat не ответил — оставим прошлое и попробуем в следующий раз
        else:
            facts_cache[key] = {"t": iso(NOW), "story": story}
    elif c:
        story = c.get("story")
    items15 = ((d.get("news") or {}).get("items") or [])[:15]
    for f in story or []:
        if not f.get("src"):
            f["src"] = fact_source(f, items15)  # старые факты из кэша — тоже со ссылкой на новость
    facts, seen = [], []
    for f in (story or []) + stats_facts:
        tok = title_tokens(f["text"])
        if any(len(tok & o) / max(1, len(tok | o)) >= 0.5 for o in seen):
            continue  # тот же смысл уже есть
        seen.append(tok)
        facts.append(f)
    return facts[:20] or None

# ---------------------------------------------------------------- main

_mvp_new = [0]
MVP_PER_RUN = int(os.environ.get("MVP_PER_RUN", "80"))  # страниц игроков HLTV за полный сбор (темп тот же — раз в 2,5 с)


def hltv_players_warm() -> None:
    """Очередь страниц игроков HLTV: сначала те, о ком ничего нет, потом давно не обновлённые; ближайшие матчи — вперёд.
    Отказавшие недавно страницы ждут своей паузы и не стоят в начале. Всё полученное до остановки остаётся в кэше,
    следующий сбор продолжает с того же места очереди."""
    for k, v in list(hltv_cache.items()):  # id игроков со страниц команд и рейтинга из прошлых сборов
        if k.startswith("tpl:") or k == "rkpl":
            for n, x in ((v or {}).get("data") or {}).items():
                hltv_player_ids.setdefault(n, x)
    nicks: list = []
    files = sorted((load_json(f, {}) or {} for f in (OUT / "match").glob("*.json")), key=lambda d: d.get("begin_at") or "9999")
    for d in files:
        for t in d.get("teams") or []:
            nicks += [p.get("nick") for p in (t.get("players") or []) + (t.get("coach") or [])]
    for f in sorted((OUT / "team").glob("*.json")):
        d = load_json(f, {}) or {}
        nicks += [p.get("nick") for p in (d.get("roster") or []) + (d.get("coach") or [])]
    queue = []
    for i, n in enumerate(dict.fromkeys(x for x in nicks if x)):
        pl = hltv_player_ids.get(norm(n))
        if not pl:
            continue
        c = hltv_cache.get(f"pinfo5:{pl['id']}")
        old = any(hltv_cache.get(f"{v}:{pl['id']}") for v in ("pinfo4", "pinfo3", "pinfo2", "pinfo"))
        rank = 0 if not c and not old else 1 if not c else 2  # ничего нет → старая версия → давно не обновлялось
        if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(days=7):
            continue
        queue.append((rank, (c or {}).get("t") or "", i, n))
    queue.sort()
    done = 0
    for _, _, _, n in queue:
        if _mvp_new[0] >= MVP_PER_RUN or _hltv_blocked[0]:
            break
        try:
            if hltv_player_info(n):
                done += 1
        except Exception as e:
            warn(f"Игрок HLTV {n}: {e!r}")
    print(f"HLTV игроки: в очереди {len(queue)}, обновлено {done}, запросов {_mvp_new[0]}" + (" (HLTV попросил остановиться)" if _hltv_blocked[0] else ""))


def hltv_player_info(nick: str) -> dict | None:
    """Со страницы игрока HLTV: медали MVP, сколько мейджоров сыграл и выиграл. Кэш на неделю, до 12 новых страниц за запуск."""
    pl = hltv_player_ids.get(norm(nick))
    if not pl:
        return None
    key = f"pinfo5:{pl['id']}"
    c = hltv_cache.get(key)
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(days=7):
        return c["data"]
    c = c or hltv_cache.get(f"pinfo4:{pl['id']}") or hltv_cache.get(f"pinfo3:{pl['id']}") or hltv_cache.get(f"pinfo2:{pl['id']}") or hltv_cache.get(f"pinfo:{pl['id']}")  # старая запись (без названий мейджоров) — пока не перечитали
    path = f"/player/{pl['id']}/{pl['slug']}"
    if _mvp_new[0] >= MVP_PER_RUN or _hltv_blocked[0] or STATS or hltv_page_skip(path):
        return c["data"] if c else None  # лимит, стоп или страница недавно отказывала — берём что есть
    _mvp_new[0] += 1
    h = hltv_get(path)
    if not h:
        return c["data"] if c else None
    b = re.sub(r"\s+", " ", h)
    mvp = 0
    m = re.search(r'title="MVP winner at:[^"]*"[^>]*>(?:<img[^>]*>)?\s*<div class="mvp-count">(\d+)</div>', b)
    if m:
        mvp = int(m.group(1))
    elif re.search(r'title="MVP winner at:', b):
        mvp = 1
    tm = re.search(r'<div class="stat">(\d+)</div><div class="description">Total MVPs', b)
    if tm:
        mvp = int(tm.group(1))
    ev = []
    i = b.find('id="MVPs"')
    if i >= 0:
        seg = b[i:i + 40000]
        j = seg.find("</table>")
        for e in re.findall(r'<div class="trophy-event"><a [^>]*>([^<]+)</a>', seg[:j if j > 0 else None]):
            e = _html.unescape(e).strip()
            if e and e not in ev:
                ev.append(e)
    if ev:
        mvp = max(mvp, len(ev))
    pm = re.search(r'<div class="stat">(\d+)</div><div class="description">Majors? played', b)
    wm = re.search(r'<div class="stat">(\d+)</div><div class="description">Majors? won', b)
    # места на турнирах — таблица .achievement-table (так её разбирают gigobyte/HLTV и hltv-scraper-api):
    # строка .team, место в .achievement, турнир в .tournament-name-cell a
    mj, wins = [], []
    k = b.find("achievement-table")
    if k >= 0:
        seg = b[k:k + 120000].split("</table>", 1)[0]
        for row in re.split(r'<tr[^>]*class="[^"]*\bteam\b', seg)[1:]:
            # место: <div class="text-ellipsis achievement "><i class="fa fa-trophy "></i>1/4 final</div> (разметка HLTV, 11.2026)
            m_pl = re.search(r'class="[^"]*\bachievement\b[^"]*"[^>]*>(?:\s*<i[^>]*>\s*</i>)?\s*([^<]+?)\s*<', row)
            m_ev = re.search(r'class="tournament-name-cell"[^>]*>.*?<a [^>]*href="/events/\d+/[^"]*"[^>]*>(?:<[^>]+>)*\s*([^<]+?)\s*<', row)
            if not m_ev:
                continue
            name, place = _html.unescape(m_ev.group(1)).strip(), (m_pl.group(1).strip() if m_pl else "")
            if "major" in name.lower():
                lab = name + (f" — {place}" if place else "")
                if lab not in mj:
                    mj.append(lab)
            if re.match(r"1st$|winner", place, re.I) and name not in wins:
                wins.append(name)
    # трофеи игрока: ряд .trophy-row-trophy (название — в title картинки) и подписи span.trophyDescription
    tro = []
    cells = re.findall(r'<td class="trophy-row-trophy"[^>]*>(.*?)</td>', b)  # в ячейке команды несколько трофеев подряд
    for t in [x for c0 in cells for x in re.findall(r'<img[^>]*title="([^"]+)"', c0)] + re.findall(r'class="trophyDescription"[^>]*title="([^"]+)"', b):
        t = _html.unescape(t).strip()
        if t and t not in tro:
            tro.append(t)
    if not mj:  # запасной разбор: любые ссылки на события со словом Major
        for e in re.findall(r'<a href="/events/\d+/[^"]+"[^>]*>(?:<[^>]+>)*\s*([^<]*Major[^<]*)<', b):
            e = _html.unescape(e).strip()
            if e and re.search(r"20\d\d", e) and e not in mj:
                mj.append(e)
    yr = lambda x: (re.findall(r"20\d\d", x) or ["0"])[-1]
    mj.sort(key=yr, reverse=True)
    old = (c or {}).get("data") or {}
    if not mj and old.get("major_events"):  # новый разбор ничего не нашёл — старый список мажоров не теряем
        mj = list(old["major_events"])
    d = {"mvp": mvp, "mvp_events": ev, "majors": int(pm.group(1)) if pm else 0, "major_wins": int(wm.group(1)) if wm else 0,
         "major_events": mj, "trophies": tro[:40], "event_wins": wins[:40]}
    d["mvp_events"] = [str(x) for x in d["mvp_events"]] if isinstance(d["mvp_events"], list) else []
    hltv_cache[key] = {"t": iso(NOW), "data": d}
    return d


_d2_recent: list = []


def d2_recent_matches() -> list[dict]:
    """Лента результатов dust2.us за последний месяц (одна страница, ~350 матчей): id и slug. Без HLTV."""
    if not _d2_recent:
        h = re.sub(r"\s+", " ", d2_get("/results") or "")
        dates = {mid: int(ms) for mid, ms in re.findall(r'href="/matches/(\d+)/[^"#?]+" class="match-time-info">.{0,300}?data-date="(\d{12,})"', h)}
        seen = set()
        for mid, slug in re.findall(r'href="/matches/(\d+)/([^"#?]+)"', h):
            if mid not in seen:
                seen.add(mid)
                dt = iso(datetime.fromtimestamp(dates[mid] / 1000, tz=timezone.utc)) if mid in dates else None
                _d2_recent.append({"id": mid, "slug": slug, "day": None, "date": dt})
        _d2_recent.append(None)  # метка «уже загружали», даже если пусто
    return [x for x in _d2_recent if x]


def attach_veto(sd: dict) -> None:
    """Полное вето серии (dust2.us): кто что банил и пикал, какая карта осталась третьей."""
    if sd.get("veto") is not None:
        return
    names = [(t or {}).get("name") or "" for t in sd.get("teams") or []]
    d0 = parse_iso(sd.get("date"))
    if len(names) < 2 or not d0:
        return
    def variants(n):
        return {x for x in (norm(n), norm(re.sub(r"^(the|team)\s+|\s+(team|esports|gaming)$", "", n, flags=re.IGNORECASE))) if len(x) >= 2}
    va, vb = variants(names[0]), variants(names[1])
    # 1) последний месяц — прямо с ленты dust2, без HLTV
    if NOW - d0 < timedelta(days=28):
        for mt in d2_recent_matches():
            sl = norm(mt["slug"])
            if not (any(x in sl for x in va) and any(x in sl for x in vb)):
                continue
            v = veto_cache.get(f"match:{mt['id']}")
            if not (v and any(x.get("team") for x in v.get("steps") or [])):
                v = hltv_veto(mt)
            if v and not v.get("date") and mt.get("date"):
                v["date"] = mt["date"]  # в кэше вето без даты — берём из ленты
            vd = parse_iso((v or {}).get("date"))
            if v and v.get("steps") and vd and abs((vd - d0).total_seconds()) < 40 * 3600:
                sd["veto"] = v["steps"]
                return
    # старше месяца не ищем: владельцу не нужно, а это лишние запросы к HLTV


ODDS_LOG_F = OUT / "odds_log.json"
odds_log: dict = {}


def odds_log_put(m: dict, bids: list, win: list, provider) -> None:
    """Журнал прогнозов: последняя линия до начала матча — чтобы потом сравнить с итогом."""
    if not odds_log:
        odds_log.update((load_json(ODDS_LOG_F, {}) or {}).get("entries") or {})
    st = parse_iso(m.get("start_date"))
    if not st or st < NOW:
        return  # после начала линия уже лайвовая — в журнал не пишем
    e = odds_log.setdefault(str(m.get("id")), {})
    e.update({"slug": m.get("slug"), "begin": m.get("start_date"), "ids": bids, "p": win, "prov": provider, "t": iso(NOW)})


def odds_log_resolve() -> None:
    """Итоги матчей из журнала (bo3.gg) и сводка: как часто фаворит букмекера побеждал."""
    if not odds_log:
        odds_log.update((load_json(ODDS_LOG_F, {}) or {}).get("entries") or {})
    if not odds_log:
        return
    n_new = 0
    for k, e in sorted(odds_log.items(), key=lambda kv: kv[1].get("begin") or ""):
        if "w" in e or n_new >= 60:
            continue
        st = parse_iso(e.get("begin"))
        if not st or st > NOW - timedelta(hours=5):
            continue
        try:
            x = bo3(f"/matches/{e['slug']}")
        except Exception:
            x = None
        n_new += 1
        if not x:
            continue
        w = x.get("winner_team_id")
        if not w and x.get("status") == "finished" and x.get("team1_score") is not None:
            w = x.get("team1_id") if (x.get("team1_score") or 0) > (x.get("team2_score") or 0) else x.get("team2_id")
        ids = e.get("ids") or []
        if w and w in ids:
            e["w"] = ids.index(w)
        elif st < NOW - timedelta(days=3):
            e["w"] = None  # отменён или без итога — больше не спрашиваем
    for k in [k for k, e in odds_log.items() if (parse_iso(e.get("begin")) or NOW) < NOW - timedelta(days=120)]:
        del odds_log[k]
    done = [e for e in odds_log.values() if e.get("w") in (0, 1) and e.get("p")]
    summ = {"n": len(done)}
    if done:
        fav = [e for e in done if e["p"][0] != e["p"][1]]
        summ["fav_won"] = round(100 * sum(1 for e in fav if e["p"][e["w"]] > e["p"][1 - e["w"]]) / max(1, len(fav)))
        summ["brier"] = round(sum((e["p"][0] / 100 - (1 if e["w"] == 0 else 0)) ** 2 for e in done) / len(done), 3)
        bins = {}
        for e in done:
            pf = max(e["p"]); b = min(90, pf // 10 * 10)
            c = bins.setdefault(f"{b}-{b + 9}", [0, 0]); c[0] += 1; c[1] += 1 if e["p"][e["w"]] == pf else 0
        summ["by_prob"] = {kb: {"n": c[0], "fav_won": round(100 * c[1] / c[0])} for kb, c in sorted(bins.items())}
    save_json(ODDS_LOG_F, {"updated": iso(NOW), "summary": summ, "entries": odds_log})
    print(f"Прогнозы букмекера: сыграно {summ['n']}, фаворит победил {summ.get('fav_won', '—')}%, Брайер {summ.get('brier', '—')}")


def devig(coeffs: list) -> list:
    """Коэффициенты → честные вероятности. Не делим наценку поровну, а степенным методом: букмекер (особенно
    с высокой наценкой, как Thunderpick) закладывает больше маржи в андердога, поэтому фаворит у него недооценён.
    Ищем k: сумма (1/c)^k = 1 — так получаются вероятности ближе к линии точных букмекеров (Pinnacle)."""
    q = [1 / c for c in coeffs if c and c > 1]
    if len(q) != len(coeffs) or sum(q) <= 1:
        t = sum(q) or 1
        return [x / t for x in q] if q else []
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(x ** k for x in q) > 1:
            lo = k
        else:
            hi = k
    p = [x ** ((lo + hi) / 2) for x in q]
    t = sum(p)
    return [x / t for x in p]


_bo3_starter: dict = {}


def bo3_odds(bids: list) -> dict | None:
    """Коэффициенты букмекеров на ближайший матч двух команд (bo3.gg) → вероятности победы и точного счёта."""
    if len(bids) != 2 or not all(bids):
        return None
    try:
        res = bo3("/matches", {"page[limit]": 20, "sort": "start_date", "filter[matches.status][in]": "upcoming,current",
                               "filter[matches.team_ids][overlap]": bids[0]}).get("results") or []
    except Exception as e:
        warn(f"bo3 коэффициенты: {e!r}")
        return None
    m = next((x for x in res if {x.get("team1_id"), x.get("team2_id")} == set(bids)), None)
    if m:  # вето обычно начинает вторая команда в паре bo3.gg (так в ~72% из 357 серий BO3)
        _bo3_starter[tuple(bids)] = bids.index(m.get("team2_id")) if m.get("team2_id") in bids else 0
    bu = (m or {}).get("bet_updates") or {}
    c1, c2 = (bu.get("team_1") or {}).get("coeff"), (bu.get("team_2") or {}).get("coeff")
    if not c1 or not c2 or min(c1, c2) < 1.02:
        return None  # нет линии или матч уже фактически решён
    flip = m.get("team1_id") == bids[1]
    p1 = devig([c1, c2])[0]
    out = {"win": [round(100 * (1 - p1 if flip else p1)), round(100 * (p1 if flip else 1 - p1))], "provider": bu.get("provider")}
    try:
        odds_log_put(m, bids, out["win"], bu.get("provider"))
    except Exception as e:
        warn(f"Журнал прогнозов: {e!r}")
    sc = {}
    for mk in bu.get("additional_markets") or []:
        mt = re.match(r"score_(\d)_(\d)$", mk.get("bet_type") or "")
        if mt and mk.get("coeff") and mk["coeff"] > 1.0:
            a, b = int(mt.group(1)), int(mt.group(2))
            sc[(b, a) if flip else (a, b)] = 1 / mk["coeff"]
    if len(sc) >= 3:
        ks = list(sc.keys()); pv = devig([1 / sc[k] for k in ks])
        sc = dict(zip(ks, pv))
        out["scores"] = [{"score": f"{a}:{b}", "p": round(100 * v)} for (a, b), v in sorted(sc.items(), key=lambda kv: -kv[1])]
    return out


def build_prediction(d: dict, bids: list) -> dict | None:
    """Вероятное вето (по вето команд за 3 месяца), счёт на картах (по винрейту карт) и исходы серии."""
    V = d.get("veto") or {}
    vt = V.get("teams") or []
    table = {r["map"]: r for r in (d.get("maps") or {}).get("table") or []}
    if len(vt) < 2 or not all(vt) or not table:
        return None
    seen: dict = {}
    for t in vt:
        for key in ("picks", "bans", "deciders"):
            for mp, c in t.get(key) or []:
                seen[mp] = seen.get(mp, 0) + c
    pool = [mp for mp, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:7]
    if len(pool) < 7:
        pool += [mp for mp in table if mp not in pool][:7 - len(pool)]
    def wr(i, mp):  # винрейт на карте со «сжатием» к 50%: при малом числе игр крайним значениям не верим
        r = table.get(mp)
        t = r["teams"][i] if r else {"won": 0, "played": 0}
        return (t["won"] + 1.5) / (t["played"] + 3)
    def pmap(mp):  # вероятность, что карту выиграет первая команда (log5)
        a, b = wr(0, mp), wr(1, mp)
        return a * (1 - b) / (a * (1 - b) + b * (1 - a))
    # Выбор на каждом шаге вето (проверено на ~390 сериях с настоящим вето dust2: десайдер угадывается в ~34% против 24%
    # у прежнего способа, пики — 43% против 41%; см. tools/veto_backtest.py):
    #   бан  = как часто команда банит карту (свежие матчи весомее) + её первый бан + насколько соперник на карте сильнее;
    #   пик  = как часто команда пикает карту + насколько она на карте сильнее соперника + что уже пикала именно против него.
    bans_done = [0, 0]
    opp_key = [norm(vt[1].get("team")), norm(vt[0].get("team"))]
    def choose(i, act, left):
        t = vt[i]
        if not t.get("pr") and not t.get("br"):  # старые данные без весов — как раньше
            cnt = dict(t.get("bans" if act == "ban" else "picks") or [])
            if act == "ban":
                for mp, c in t.get("first_bans") or []:
                    cnt[mp] = cnt.get(mp, 0) + c
            own = (lambda mp: wr(i, mp)) if act == "pick" else (lambda mp: -wr(i, mp))
            return max(left, key=lambda mp: (cnt.get(mp, 0), own(mp)))
        def sc(mp):
            dx = wr(i, mp) - wr(1 - i, mp)
            if act == "ban":
                v = (t.get("br") or {}).get(mp, 0) - 2 * dx
                if bans_done[i] == 0:
                    v += (t.get("fr") or {}).get(mp, 0)
                return v
            return (t.get("pr") or {}).get(mp, 0) + 2 * dx + 0.1 * ((t.get("vs") or {}).get(opp_key[i]) or {}).get(mp, 0)
        mp = max(left, key=sc)
        if act == "ban":
            bans_done[i] += 1
        return mp
    odds = bo3_odds(bids) if len(bids) == 2 and all(bids) else None
    st0 = _bo3_starter.get(tuple(bids), 0) if len(bids) == 2 else 0
    bo = int(d.get("bo") or 3)
    order = {1: [(0, "ban"), (1, "ban"), (0, "ban"), (1, "ban"), (0, "ban"), (1, "ban")],
             3: [(0, "ban"), (1, "ban"), (0, "pick"), (1, "pick"), (0, "ban"), (1, "ban")],
             5: [(0, "ban"), (1, "ban"), (0, "pick"), (1, "pick"), (0, "pick"), (1, "pick")]}.get(bo)
    if not order:
        return None
    if st0 == 1:
        order = [(1 - i, act) for i, act in order]
    left, steps, maps = list(pool), [], []
    for i, act in order:
        if not left:
            break
        mp = choose(i, act, left)
        left.remove(mp)
        steps.append({"team": i, "act": act, "map": mp})
        if act == "pick":
            maps.append({"map": mp, "by": i})
    if left:
        steps.append({"team": None, "act": "decider", "map": left[0]})
        maps.append({"map": left[0], "by": None})
    # Без коэффициентов: основа — разница очков VRS (проверка на 122 сериях последних 45 дней: угадан победитель в 74%,
    # Брайер 0.18), а винрейты на картах лишь слегка сдвигают карту — сами по себе они почти не лучше монетки.
    # С коэффициентами — карты считаются от шанса букмекера на серию (так проценты карт с ним согласованы).
    pv = None
    try:
        ps = None
        if odds and odds.get("win"):
            ps = min(0.97, max(0.03, odds["win"][0] / 100))
        else:
            tbl = vrs_table()
            ra, rb = (vrs_find(t, tbl) if tbl else None for t in (d.get("teams") or [{}, {}])[:2])
            if ra and rb and ra.get("points") and rb.get("points"):
                ps = 1 / (1 + 10 ** (-(ra["points"] - rb["points"]) / 400))
        if ps is not None:
            lo, hi = 0.0, 1.0  # вероятность выиграть карту, при которой серия BO3 выигрывается с вероятностью ps
            for _ in range(40):
                mid = (lo + hi) / 2
                ser = mid if bo == 1 else mid ** 3 * (10 - 15 * mid + 6 * mid * mid) if bo == 5 else mid * mid * (3 - 2 * mid)
                if ser < ps:
                    lo = mid
                else:
                    hi = mid
            pv = (lo + hi) / 2
    except Exception as e:
        warn(f"Прогноз по VRS: {e!r}")
    def pmap2(mp):
        if pv is None:
            return pmap(mp)
        lg = math.log(pv / (1 - pv)) + 1.0 * (wr(0, mp) - wr(1, mp))
        return 1 / (1 + math.exp(-lg))
    probs = [pmap2(x["map"]) for x in maps]
    for x, p in zip(maps, probs):
        r = 0.5 + (p - 0.5) * 0.25  # вероятность раунда из вероятности карты — грубая оценка
        lose = max(3, min(11, round(13 * (1 - max(r, 1 - r)) / max(r, 1 - r))))
        x["p"] = round(100 * p)
        x["score"] = [13, lose] if p >= 0.5 else [lose, 13]
    need = bo // 2 + 1
    dist: dict = {}
    def walk(k, a, b, pr):
        if a == need or b == need or k >= len(probs):
            dist[(a, b)] = dist.get((a, b), 0) + pr
            return
        walk(k + 1, a + 1, b, pr * probs[k])
        walk(k + 1, a, b + 1, pr * (1 - probs[k]))
    walk(0, 0, 0, 1.0)
    outcomes = [{"score": f"{a}:{b}", "p": round(100 * v)} for (a, b), v in sorted(dist.items(), key=lambda kv: -kv[1])]
    res = {"steps": steps, "maps": maps, "outcomes": outcomes, "source": "статистика"}
    if odds:
        res["odds"] = odds
        if odds.get("scores"):
            res["outcomes"], res["source"] = odds["scores"], "букмекеры"
            res["provider"] = odds.get("provider")
    top = res["outcomes"][0]["score"] if res["outcomes"] else ""
    if bo == 3 and top in ("2:0", "0:2") and len(maps) == 3:
        maps[2]["score"] = [0, 0]  # самый вероятный исход — 2:0, до десайдера дело не дойдёт
    return res


def build_detail(m: dict, prev: dict | None) -> dict:
    brief = match_brief(m)
    teams, bids, hists = [], [], []
    for t in brief["teams"]:
        full = fetch_team(t["id"])
        players = [player_brief(p) for p in full.get("players") or [] if p.get("active", True)]
        players, coach = merge_roster(players, lp_roster({**t, "name": full.get("name") or t.get("name")}))
        try:
            add_pronunciation(players + coach)
        except Exception as e:
            warn(f"Произношение: {e!r}")
        fix_names(players)
        bid = bo3_team_id({**t, "slug": full.get("slug") or t.get("slug")})
        add_alias(bid, t.get("name"), t.get("acronym"), t.get("slug"), full.get("name"), full.get("acronym"))
        hist = bo3_history(bid) if bid else []
        if not coach and bid and len(players) > 5:  # состава с Liquipedia нет, а у PandaScore больше пятерых — оставляем тех, кто играл последние карты
            recent = set()
            for m0 in hist[:2]:
                for g in bo3_games(m0)[:3]:
                    det = details_cache.get(str(g.get("id"))) or {}
                    recent |= {norm(x.get("nick")) for x in det.get("players") or [] if x.get("tid") == bid}
            if len(recent) >= 5:
                kept = [p for p in players if norm(p.get("nick")) in recent]
                if len(kept) >= 5:
                    players = kept
        bids.append(bid)
        hists.append(hist)
        teams.append({**t, "bo3_id": bid, "players": players, "coach": coach, "form": build_form(bid, hist) if bid else []})
    detail = {**brief, "teams": teams, "updated": iso(NOW)}
    one = len(teams) < 2
    if one:  # соперник ещё не известен: карточка с одной командой, статистику пары не считаем
        nm = re.sub(r"^[^:]*:\s*", "", brief.get("name") or "")
        detail["tbd"] = True
        detail["tbd_side"] = 0 if re.match(r"(?i)^tbd\s+vs\b", nm) else 1
        if prev and prev.get("opp"):
            detail["opp"] = prev["opp"]
    if len(bids) == 2 and all(bids):
        detail["h2h"] = build_h2h(bids[0], bids[1], hists[0])
    else:
        detail["h2h"] = []
    if not one:
        detail["maps"] = build_maps(bids, hists)
        detail["sides"] = build_sides(bids, hists)
        try:
            detail["veto"] = build_veto(teams)
        except Exception as e:
            warn(f"Вето: {e!r}")
        try:
            detail["prediction"] = build_prediction(detail, bids)
        except Exception as e:
            warn(f"Прогноз: {e!r}")
    for t, bid, hist in zip(teams, bids, hists):
        t["stats"] = build_player_stats(t, bid, hist)
        for p in (t.get("players") or []) + (t.get("coach") or []):  # у тренеров тоже мажоры и MVP (чаще всего — с игровой карьеры)
            try:
                info = hltv_player_info(p.get("nick") or "")
            except Exception as e:
                warn(f"Игрок HLTV {p.get('nick')}: {e!r}")
                info = None
            if info:
                p.update({k: v for k, v in info.items() if v})
    # подробности по каждой серии из «Формы» и личных встреч — открываются по нажатию
    series = {}
    for x in detail.get("h2h") or []:
        m0 = _h2h_matches.get(x.get("sid") or "")
        if m0 and x["sid"] not in series:
            try:
                sd = series_detail(m0)
            except Exception as e:
                warn(f"Серия {x['sid']}: {e!r}")
                sd = None
            if sd:
                series[x["sid"]] = sd
    for bid, hist in zip(bids, hists):
        for m0 in hist[:10] if bid else []:
            k = str(m0.get("id"))
            if k in series:
                continue
            try:
                sd = series_detail(m0)
            except Exception as e:
                warn(f"Серия {k}: {e!r}")
                sd = None
            if sd:
                series[k] = sd
    # и по матчам из «Последних игр на карте»
    by_id = {str(m0.get("id")): m0 for hist in hists for m0 in hist}
    for name, rows in ((detail.get("maps") or {}).get("recent") or {}).items():
        for i in (0, 1):
            for g in [r for r in rows if r.get("team") == i][:8]:
                k = str(g.get("sid"))
                if k in series or k not in by_id:
                    continue
                try:
                    sd = series_detail(by_id[k])
                except Exception as e:
                    warn(f"Серия {k}: {e!r}")
                    sd = None
                if sd:
                    series[k] = sd
    for sd in series.values():
        try:
            attach_veto(sd)
        except Exception as e:
            warn(f"Вето серии: {e!r}")
    detail["series"] = series
    # место и очки VRS, оценка при разных исходах, призовые места за 3 месяца
    try:
        table = vrs_table()
    except Exception as e:
        warn(f"VRS: {e!r}")
        table = []
    rows = []
    for t, hist in zip(teams, hists):
        r = vrs_find(t, table) if table else None
        rows.append(r)
        if r:
            t["vrs"] = {"rank": r["rank"], "points": r["points"], "region": r.get("region"), "reg_rank": r.get("reg_rank")}
        try:
            t["prizes"] = prize_places((r or {}).get("hltv") or hltv_team_for(t), hist)
        except Exception as e:
            warn(f"Призовые места {t.get('name')}: {e!r}")
    if len(rows) == 2 and all(rows):
        try:
            detail["vrs_outcomes"] = vrs_outcomes_valve(rows[0], rows[1], table, hists=hists, bids=bids,
                                                        prize=(brief.get("serie") or {}).get("prize"), since=(brief.get("serie") or {}).get("begin") or "") or vrs_outcomes(rows[0], rows[1], table)
        except Exception as e:
            warn(f"VRS прогноз: {e!r}")
            detail["vrs_outcomes"] = vrs_outcomes(rows[0], rows[1], table)
    begin = parse_iso(brief["begin_at"])
    soon = begin is not None and begin - NOW <= timedelta(hours=DETAIL_HOURS)
    if STATS and prev and prev.get("news"):
        detail["news"] = prev["news"]  # новости обновляет свой быстрый сбор
    elif soon or not (prev and prev.get("news")):
        detail["news"] = build_news(teams)
    else:
        detail["news"] = prev["news"]
    if STATS and prev:
        for k in ("veto",):
            if prev.get(k) is not None:
                detail[k] = prev[k]
    if one:
        return detail
    try:
        if (soon or not prev) and brief.get("tier") == "s" and not STATS:
            detail["facts"] = build_facts(detail, prev)
        elif STATS and prev:
            detail["facts"] = prev.get("facts")
        else:
            detail["facts"] = prev.get("facts") or (code_facts(detail)[:20] or None)  # до матча ещё далеко — нейросеть не тратим
    except Exception as e:
        warn(f"Факты: {e!r}")
    return detail


TEAM_GAMES_NEW = [0]
_team_pron_new = [0]  # команд за сбор, для которых просим у нейросети русские имена (лимит ниже — до 100)
_team_lp_new = [0]


def team_by_name(name: str) -> int | None:
    key = "n:" + norm(name)
    if teams_map.get(key):
        return teams_map[key]
    n = norm(name)
    for b0, names in team_aliases.items():  # уже встречали эту команду под другим написанием (BETBOOM → BetBoom Team)
        if n in names or norm(re.sub(r"\s+(team|esports|gaming|clan)$", "", name, flags=re.IGNORECASE)) in names:
            teams_map[key] = b0
            return b0
    bid = None
    for nm in dict.fromkeys([name, name.title(), name + " Team", name.title() + " Team"]):
        bid = bo3_team_id({"id": key, "name": nm})
        if bid:
            break
    if bid:
        teams_map[key] = bid
    return bid


def build_team_page(r: dict) -> dict | None:
    """Страница команды из рейтинга: место, состав, форма, карты, призовые."""
    bid = team_by_name(r["name"])
    if not bid:
        return None
    add_alias(bid, r["name"])
    hist = bo3_history(bid, pages=2)
    form = build_form(bid, hist)
    series = {}
    for m0 in hist[:10]:
        try:
            sd = series_detail(m0)
        except Exception as e:
            warn(f"Серия {m0.get('id')}: {e!r}")
            sd = None
        if sd:
            series[str(m0.get("id"))] = sd
    maps = build_maps([bid], [hist], max_matches=30)
    for row in maps.get("table") or []:
        row["teams"] = row["teams"][:1]
    players = list((bo3_team_players(bid, hist) or {}).values())
    roster, coach = [], []
    key = "n:" + norm(r["name"])
    # призовые и id игроков с HLTV — раньше состава: по этим id ищутся MVP и мейджоры игроков
    ach = []
    if r.get("hltv") and (hltv_cache.get(f"ach:{r['hltv']['id']}") or TEAM_GAMES_NEW[0] < 30):
        TEAM_GAMES_NEW[0] += 1
        try:
            ach = prize_places(r["hltv"], hist)
        except Exception as e:
            warn(f"Призовые {r['name']}: {e!r}")
    if roster_cache.get(key) or _team_lp_new[0] < 40:
        if not roster_cache.get(key):
            _team_lp_new[0] += 1
        try:
            roster, coach = merge_roster([], lp_roster({"id": key, "name": r["name"]}))
            fix_names(roster)
            for p in roster + coach:
                info = hltv_player_info(p.get("nick") or "")
                if info:
                    p.update({k: v for k, v in info.items() if v})
            for p in roster:
                full = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
                pc = pron_cache.get(norm(p.get("nick")) + "|" + norm(full)) or {}
                if pc.get("name_ru") and not p.get("name_ru"):
                    p["name_ru"] = pc["name_ru"]
        except Exception as e:
            warn(f"Состав {r['name']}: {e!r}")
    # имена по-русски для всех, у кого есть настоящее имя (нейросеть один раз на человека, дальше — кэш навсегда)
    if roster and not STATS and _team_pron_new[0] < 100:
        need = [p for p in roster + coach if (p.get("first_name") or p.get("last_name") or p.get("name")) and not p.get("name_ru")]
        if need:
            _team_pron_new[0] += 1
            try:
                add_pronunciation(need)
            except Exception as e:
                warn(f"Имена по-русски {r['name']}: {e!r}")
    roster_src = "lp" if roster else "bo3"
    if not roster and players:  # состава с Liquipedia нет — строим строки из игроков bo3, чтобы было как во вкладке «Игроки»
        # только те, кто играл последний матч команды: запасные и ушедшие (они есть в истории карт) не попадают
        last = []
        for m0 in hist[:3]:
            sd = series.get(str(m0.get("id")))
            if not sd or not sd.get("players"):
                continue
            side = 0 if side_of(m0, bid) == 1 else 1
            last = [r[0] for r in (sd["players"][side] or [])]
            if len(last) >= 5:
                break
        if len(last) >= 5:
            keep = {norm(x) for x in last}
            players = [n for n in players if norm(n if isinstance(n, str) else n.get("nick")) in keep] or last
        roster = [{"nick": n if isinstance(n, str) else (n.get("nick") or "")} for n in players]
        roster = [p for p in roster if p["nick"]]
        for p in roster:
            info = hltv_player_info(p["nick"])
            if info:
                p.update({k: v for k, v in info.items() if v})
            pc = next((v for k, v in pron_cache.items() if k.startswith(norm(p["nick"]) + "|") and v.get("name_ru")), None)
            if pc:
                p["name_ru"] = pc["name_ru"]
    return {"updated": iso(NOW), "name": r["name"], "vrs": {k: r.get(k) for k in ("rank", "points", "region", "reg_rank", "hltv_rank")},
            "players": players or r.get("roster") or [], "roster": roster, "coach": coach, "logo": r.get("logo"), "roster_src": roster_src,
            "stats": build_player_stats({"players": roster}, bid, hist) if roster else [],
            "form": form, "series": series, "upcoming": bo3_upcoming(bid),
            "maps": {"window_days": maps.get("window_days"), "table": maps.get("table") or []}, "prizes": ach}


def team_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(name) or name.lower()).strip("-") or "team"


def build_ranking() -> dict:
    """Вкладка «Рейтинг»: топ-100 VRS (живой с HLTV, иначе официальный снимок Valve) и рейтинг HLTV."""
    vrs = vrs_table()
    src, date = "HLTV · живой рейтинг Valve", None
    if len(vrs) < 100:
        md = valve_md_table()
        if len(md.get("teams") or []) > len(vrs):
            vrs, src, date = md["teams"], "Valve · официальный рейтинг", md.get("date")
    vrs = vrs[:100]
    hl = []  # рейтинг HLTV не показываем — только VRS
    hm0 = {norm(r["name"]): r["rank"] for r in hl}
    for r in vrs:
        r["hltv_rank"] = hm0.get(norm(r["name"]))
    (OUT / "team").mkdir(parents=True, exist_ok=True)
    keep = set()
    for r in vrs:
        r["slug"] = team_slug(r["name"])
        path = OUT / "team" / f"{r['slug']}.json"
        try:
            page = build_team_page(r)
        except Exception as e:
            warn(f"Команда {r['name']}: {e!r}")
            page = None
        if page:
            save_json(path, page)
        if path.exists():
            keep.add(path.name)
            r["page"] = True
    for f in (OUT / "team").glob("*.json"):
        if f.name not in keep:
            f.unlink()
    return {"updated": iso(NOW), "vrs": {"source": src, "date": date, "teams": vrs}}


def finished_tournaments() -> list[dict]:
    """Сыгранные турниры тир-1 этого года — для вкладки «Завершённые»."""
    try:
        res = bo3("/tournaments", {"page[limit]": 30, "sort": "-start_date", "filter[tournaments.tier][in]": ",".join(sorted(TIERS)),
                                   "filter[tournaments.status][in]": "finished"}).get("results") or []
    except Exception as e:
        warn(f"bo3 завершённые турниры: {e!r}")
        return []
    out = []
    for t in res:
        b, e = parse_iso(t.get("start_date")), parse_iso(t.get("end_date"))
        if not b or b.year != NOW.year:
            continue
        if (t.get("tier") or "").lower() != "s":
            rj = load_json(OUT / "results" / f"bo3-{t['id']}.json", {}) or {}
            tms = [x.get("name") for m in rj.get("matches") or [] for x in m.get("teams") or []]
            if not tier2_ok(tms or bo3_tour_teams(t)):
                continue
        out.append({"id": "bo3-" + str(t["id"]), "name": (t.get("name") or "").replace("Intel Extreme Masters", "IEM"),
                    "tier": (t.get("tier") or "").lower(), "begin": iso(b), "end": iso(e) if e else None,
                    "prize": None, "prize_usd": t.get("prize"), "matches": 0, "next": None, "live": 0, "finished": True})
    return out


# ---------------------------------------------------------------- логотипы: цветные с bo3.gg, потом PandaScore, потом HLTV
LOGO_LOOKUPS = int(os.environ.get("LOGO_LOOKUPS", "150"))  # поисков команд на bo3.gg за запуск (bo3 — открытый API)


def logo_key(name: str | None) -> str:
    n = re.sub(r"^(the|team)\s+|\s+(team|esports|gaming)$", "", (name or "").lower())
    return re.sub(r"[^a-z0-9а-я]", "", n)


def logo_rank(url: str, src: str) -> int:
    if src == "bo3":
        return 0
    if src == "panda":
        return 3 if "lightmode" in url else 1  # «lightmode» у PandaScore — тёмный логотип для светлого фона
    return 2


def build_positions() -> None:
    """data/positions.json: позиции игроков по картам с ProRoles (GameSenseLab, по демо турниров).
    Один открытый файл, обновляем не чаще раза в 20 часов: 2 запроса в сутки."""
    path = OUT / "positions.json"
    old = load_json(path, None) or {}
    if old.get("updated") and old["updated"] > iso(NOW - timedelta(hours=20)):
        return
    base = "https://proroles.gamesenselab.pro"
    page = http_get(base + "/", {"User-Agent": "cs-prep/1.0 (+https://github.com/vladrimskij-a11y/cs-prep)"}).decode("utf-8", "replace")
    mv = re.search(r"PP_VER\s*=\s*'(\w+)'", page)
    raw = http_get(base + "/pp/index.json" + (f"?v={mv.group(1)}" if mv else ""), {"User-Agent": "cs-prep/1.0 (+https://github.com/vladrimskij-a11y/cs-prep)", "Accept": "application/json"})
    d = json.loads(raw)

    def top(lst, n):
        if not lst or not n:
            return None
        a, c = lst[0][0], lst[0][-1]
        return [a, round(100 * c / n)]

    teams: dict = {}
    days = []
    for m in d.get("maps") or []:
        mid = str(m.get("id") or "").lower()
        for s0 in m.get("src") or []:
            days.append(s0.get("day") or "")
        for p in m.get("players") or []:
            tn = p.get("team") or ""
            if not tn or tn.lower() in ("other team", "private"):
                continue
            ct, t = p.get("ct") or {}, p.get("t") or {}
            row = {"ct": (p.get("ctRole") or [""])[0], "t": (p.get("tRole") or [""])[0],
                   "cs": top(ct.get("setup"), ct.get("n")), "ts": top(t.get("early"), t.get("n")),
                   "cn": ct.get("n") or 0, "tn": t.get("n") or 0}
            if (p.get("awp") or 0) >= 0.3:
                row["awp"] = round(100 * p["awp"])
            if "Entry" in (p.get("roles") or []):
                row["entry"] = 1
            if p.get("open"):
                row["op"] = [p["open"], p.get("openwin") or 0]
            teams.setdefault(tn, {}).setdefault(mid, {})[p.get("name") or ""] = row
    out = {"updated": iso(NOW), "source": "ProRoles · GameSenseLab", "url": base,
           "maps": [str(m.get("id") or "").lower() for m in d.get("maps") or []], "teams": teams}
    save_json(path, out)
    print(f"Позиции ProRoles: команд {len(teams)}, карт {len(out['maps'])}")


def build_logos() -> None:
    """data/logos.json: название команды → лучший (цветной) логотип. Недостающие ищем на bo3.gg."""
    path = OUT / "logos.json"
    L = load_json(path, {}) or {}
    logos, srcs, tried = L.get("logos") or {}, L.get("src") or {}, L.get("tried") or {}

    def put(name, url, src):
        k = logo_key(name)
        if not k or not url:
            return
        if k not in logos or logo_rank(url, src) < logo_rank(logos[k], srcs.get(k, "")) or (src == srcs.get(k) and url != logos[k]):
            logos[k], srcs[k] = url, src

    names = set()
    for f in (OUT / "results").glob("*.json"):
        for m in (load_json(f, {}) or {}).get("matches") or []:
            for t in m.get("teams") or []:
                put(t.get("name"), t.get("logo"), "bo3"); names.add(t.get("name"))
    lst = load_json(OUT / "matches.json", {}) or {}
    for m in lst.get("matches") or []:
        for t in m.get("teams") or []:
            put(t.get("name"), t.get("image"), "panda"); names.add(t.get("name"))
    for t in (((load_json(OUT / "ranking.json", {}) or {}).get("vrs") or {}).get("teams") or []):
        put(t.get("name"), t.get("logo"), "hltv"); names.add(t.get("name"))
    # у кого нет логотипа с bo3 — ищем там по названию (неудачи повторяем раз в неделю)
    left = LOGO_LOOKUPS
    for nm in sorted(n for n in names if n):
        k = logo_key(nm)
        if srcs.get(k) == "bo3" or left <= 0:
            continue
        if (parse_iso(tried.get(k)) or NOW - timedelta(days=30)) > NOW - timedelta(days=7):
            continue
        tried[k] = iso(NOW)
        for params in ({"filter[teams.name][eq]": nm}, {"filter[teams.slug][eq]": re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")}):
            left -= 1
            try:
                res = bo3("/teams", params).get("results") or []
            except Exception as e:
                warn(f"bo3 логотип {nm}: {e}")
                break
            res = [r for r in res if r.get("image_url")]
            if len(res) == 1 or (res and logo_key(res[0].get("name")) == k):
                put(nm, res[0]["image_url"], "bo3")
                break
    save_json(path, {"updated": iso(NOW), "logos": logos, "src": srcs, "tried": tried})
    print(f"Логотипов: {len(logos)}, с bo3: {sum(1 for v in srcs.values() if v == 'bo3')}")


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
    if STATS:  # быстрый сбор: только то, что бесплатно и не блокируют
        _hltv_blocked[0] = _lp_blocked[0] = True  # dust2 не блокирует — вето ищем и в быстром сборе
        _giga["off"] = _gem["off"] = True
    matches = fetch_upcoming()
    # тир-2: оставляем только турниры, где половина участников — уровня тир-1
    ps_teams = {str(x["id"]): x.get("teams") or [] for x in fetch_future_tournaments()}
    for m in matches:
        sid = str((m.get("serie") or {}).get("id") or "")
        if sid and not ps_teams.get(sid):
            ps_teams.setdefault(sid, [])
            for o in m.get("opponents") or []:
                nm = (o.get("opponent") or {}).get("name")
                if nm and nm not in ps_teams[sid]:
                    ps_teams[sid].append(nm)
    t2_ok = {sid: tier2_ok(v) for sid, v in ps_teams.items()}
    n0 = len(matches)
    matches = [m for m in matches if ((m.get("tournament") or {}).get("tier") or "").lower() == "s"
               or t2_ok.get(str((m.get("serie") or {}).get("id") or ""), False)]
    if n0 != len(matches):
        print(f"Тир-2 без команд уровня тир-1 — скрыто матчей: {n0 - len(matches)}")
    CURRENT_TOURS[:] = list(dict.fromkeys(tournament_label(m) for m in matches))
    # id игроков HLTV из составов на страницах ближайших турниров (нужны для Rating 3.0)
    seen_tours = set()
    for m in matches:
        tname = tournament_label(m)
        if tname in seen_tours or ((m.get("tournament") or {}).get("tier") or "").lower() != "s":
            continue
        seen_tours.add(tname)
        ex = tournament_extras(tname) or {}
        for lineup in (ex.get("lineups") or {}).values():
            for p in lineup:
                hltv_player_ids[norm(p["nick"])] = {"id": p["id"], "slug": p["slug"]}
        for tn, ti in (ex.get("team_ids") or {}).items():
            hltv_team_ids[norm(tn)] = {**ti, "name": tn}
    if not STATS:  # команды из рейтинга Valve (заодно id игроков составов) — до очереди игроков
        try:
            for r in vrs_table():
                if r.get("hltv") and r.get("name"):
                    hltv_team_ids.setdefault(norm(r["name"]), {**r["hltv"], "name": r["name"]})
        except Exception as e:
            warn(f"VRS для вето: {e!r}")
    if not STATS:  # очередь игроков HLTV — до сборки карточек, чтобы они сразу взяли свежие MVP и мажоры
        try:
            hltv_players_warm()
        except Exception as e:
            warn(f"Очередь HLTV: {e!r}")
    listing = []
    keep_ids = set()
    for m in matches:
        b = match_brief(m)
        begin = parse_iso(b["begin_at"])
        # карточка — для каждого матча недели, где известны обе команды. Дальше 3 дней она «лёгкая»
        # (без фактов от нейросети, новости один раз), за 3 дня до матча — полная (см. soon в build_detail)
        detailed = len(b["teams"]) >= 1 and begin is not None  # с одной командой — тоже: соперник «победитель X/Y», статистика потом
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
        if len(b["teams"]) == 1:
            nm0 = re.sub(r"^[^:]*:\s*", "", b.get("name") or "")
            b["tbd"], b["tbd_side"] = True, 0 if re.match(r"(?i)^tbd\s+vs\b", nm0) else 1
            c0 = load_json(OUT / "match" / f"{m['id']}.json", {}) or {}
            if c0.get("opp"):
                b["opp"] = c0["opp"]
        listing.append(b)

    for f in (OUT / "match").glob("*.json"):
        if int(f.stem) not in keep_ids:
            f.unlink()

    if not STATS:
        try:
            save_json(OUT / "feed.json", build_feed())
        except Exception as e:
            warn(f"Лента: {e!r}")
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
    # турниры без матчей в ближайшую неделю — по расписанию турниров PandaScore и bo3.gg на 3 месяца вперёд
    future = [t for t in fetch_future_tournaments() if t.get("tier") == "s" or tier2_ok(t.get("teams"))]
    for t in future:
        t.pop("teams", None)
    have = {tour_norm(t["name"]) for t in list(tours.values()) + future}
    for tr in fetch_bo3_tournaments():
        if not any(tour_same(tr["name"], h) for h in have):
            future.append(tr)
    for tr in future:
        cur = tours.get(tr["id"])
        if cur:
            cur["begin"] = min(x for x in (cur.get("begin"), tr["begin"]) if x) if (cur.get("begin") or tr["begin"]) else None
            cur["end"] = max(x for x in (cur.get("end"), tr["end"]) if x) if (cur.get("end") or tr["end"]) else None
            cur["prize"] = cur.get("prize") or tr.get("prize")
        else:
            tours[tr["id"]] = tr
    tour_list = [t for t in tours.values() if not t.get("end") or (parse_iso(t["end"]) or NOW) >= NOW - timedelta(hours=12)]
    for tr in finished_tournaments():
        if not any(tour_same(tr["name"], tour_norm(t["name"])) for t in tour_list):
            tour_list.append(tr)
    tour_list.sort(key=lambda x: (x.get("begin") or x.get("next") or "9999"))
    for tr in tour_list:
        try:
            tr["bg"] = tr.get("bg") or bo3_tour_bg(tr["name"])
        except Exception as e:
            warn(f"Фон турнира {tr.get('name')}: {e!r}")
    new_ev = 0
    for tr in tour_list:
        b = parse_iso(tr.get("begin"))
        if b and b - NOW > timedelta(days=60):
            continue  # призовые и участники с HLTV — для идущих, ближайших (2 мес.) и сыгранных
        fin = bool(tr.get("finished"))
        if fin and not hltv_cache.get("event3:" + tr["name"]):
            continue  # сыгранные турниры не дособираем (решение владельца) — их обзор берётся из того, что собрали, пока они шли
        if not hltv_cache.get("event3:" + tr["name"]):
            if new_ev >= 8:
                continue  # новых страниц событий — не больше 8 за сбор, остальные в следующий раз
            new_ev += 1
        ex = tournament_extras(tr["name"], final=fin)
        print(f"Призовые/участники {tr['name']}: " + (f"мест {len((ex or {}).get('places') or [])}, команд {len((ex or {}).get('teams') or [])}" if ex else "нет (событие на HLTV не найдено или страница недоступна)"))
        if ex:
            tr["hltv"] = ex
            if ex.get("prize"):
                tr["prize_usd"] = ex["prize"]
    # сыгранные матчи идущих (и только что закончившихся) турниров — вкладка «Завершённые»
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    keep_res = set()
    for tr in tour_list:
        b, e = parse_iso(tr.get("begin")), parse_iso(tr.get("end"))
        if not b or b > NOW + timedelta(days=30):
            continue
        fid = re.sub(r"[^\w-]", "-", str(tr["id"]))
        path = OUT / "results" / f"{fid}.json"
        if e and e < NOW - timedelta(days=2) and path.exists():
            keep_res.add(path.name)  # итоги давно закончившегося турнира уже не меняются, кроме недостающего вето
            rj = load_json(path, {}) or {}
            sides_added = 0
            for sd in rj.get("matches") or []:  # раунды по сторонам — из кэша карт, без новых запросов
                gs = games_cache.get(str(sd.get("id"))) or []
                for i, mp in enumerate(sd.get("maps") or []):
                    if mp.get("sides") is None and i < len(gs):
                        sdv = map_sides(details_cache.get(str(gs[i].get("id"))), mp.get("winner"))
                        if sdv:
                            mp["sides"] = sdv
                            sides_added += 1
            if sides_added:
                save_json(path, rj)
            if not STATS:
                added = 0
                for sd in rj.get("matches") or []:
                    if not sd.get("veto"):
                        sd.pop("veto", None)
                        try:
                            attach_veto(sd)
                        except Exception as ex:
                            warn(f"Вето итога: {ex!r}")
                        added += bool(sd.get("veto"))
                if added:
                    save_json(path, rj)
                if not rj.get("stages"):  # итоги собраны до появления сетки — достраиваем сетку один раз (bo3 не блокирует)
                    try:
                        res = build_results(tr)
                        if res and res.get("stages"):
                            rj["stages"] = res["stages"]
                            save_json(path, rj)
                    except Exception as ex:
                        warn(f"Сетка {tr['name']}: {ex!r}")
            tr["results"] = len(rj.get("matches") or [])
            tr["bracket"] = bool(rj.get("stages"))  # вкладка «Обзор» у сыгранного турнира: сетка, призовые, участники
            continue
        try:
            res = build_results(tr)
        except Exception as ex:
            warn(f"Итоги {tr['name']}: {ex!r}")
            res = None
        if res and (res["matches"] or res.get("stages")):
            save_json(path, res)
        if path.exists():
            keep_res.add(path.name)
            rj = load_json(path, {}) or {}
            tr["results"] = len(rj.get("matches") or [])
            tr["bracket"] = bool(rj.get("stages"))
    for f in (OUT / "results").glob("*.json"):
        if f.name not in keep_res:
            f.unlink()
    rosters = {}
    for f in (OUT / "match").glob("*.json"):
        for t in (load_json(f, {}) or {}).get("teams") or []:
            if t.get("players"):
                rosters[norm(t["name"])] = [p["nick"] for p in t["players"]]
                if t.get("acronym"):
                    rosters[norm(t["acronym"])] = rosters[norm(t["name"])]
    try:
        attach_major(tour_list, rosters)
    except Exception as e:
        warn(f"Мейджор: {e!r}")
    major_next = None
    if not any(t.get("major", {}).get("final") and (parse_iso(t.get("end")) or NOW) >= NOW for t in tour_list if re.search(r"major", t.get("name") or "", re.I)):
        try:
            major_next = major_view(build_major(rosters))  # инвайты на следующий мейджор ещё не разосланы — прогноз по VRS
        except Exception as e:
            warn(f"Прогноз мейджора: {e!r}")
    try:
        rk = load_json(OUT / "ranking.json", None)
        active = any((x.get("tier") == "s") and x.get("has_detail") for x in listing)
        stale = not rk or (parse_iso(rk.get("updated")) or NOW) < NOW - timedelta(hours=3 if active else 12)
        if not STATS and stale:  # без турниров рейтинг и страницы команд — раз в 12 часов
            save_json(OUT / "ranking.json", build_ranking())
    except Exception as e:
        warn(f"Рейтинг: {e!r}")
    for k in [k for k, v in tg_cache.items() if (v.get("t") or "") < iso(NOW - timedelta(days=NEWS_DAYS + 3))]:
        del tg_cache[k]
    save_json(TG_CACHE_F, tg_cache)
    save_json(PRON_CACHE_F, pron_cache)
    save_json(FACTS_CACHE_F, facts_cache)
    save_json(HLTV_CACHE_F, hltv_cache)
    save_json(VETO_CACHE_F, veto_cache)
    for k in [k for k in giga_usage if k < (NOW - timedelta(days=40)).strftime("%Y-%m-%d")]:
        del giga_usage[k]
    save_json(GIGA_USAGE_F, giga_usage)
    save_json(HLTV_STATS_CACHE_F, hltv_stats_cache)
    try:
        odds_log_resolve()
    except Exception as e:
        warn(f"Журнал прогнозов: {e!r}")
    prune_caches()
    save_json(GAMES_CACHE_F, games_cache)
    save_json(DETAILS_CACHE_F, details_cache)
    save_json(BO3_PLAYERS_F, bo3_players)
    save_json(TEAMS_CACHE_F, teams_map)
    save_json(TR_CACHE_F, tr_cache)
    save_json(ROSTER_CACHE_F, roster_cache)
    for k in [k for k, v in art_cache.items() if (v.get("t") or "") < iso(NOW - timedelta(days=NEWS_DAYS + 7))]:
        del art_cache[k]
    save_json(ART_CACHE_F, art_cache)
    for k in [k for k, v in dedup_cache.items() if (v.get("t") or "") < iso(NOW - timedelta(days=2))]:
        del dedup_cache[k]
    save_json(DEDUP_CACHE_F, dedup_cache)
    try:
        d2_sync(listing)
    except Exception as e:
        warn(f"dust2 матчи: {e!r}")
    save_json(OUT / "matches.json", {
        "updated": iso(NOW),
        "tiers": sorted(TIERS),
        "tournaments": tour_list,
        "matches": listing,
        "major_next": major_next,
        "meta": stats,
    }, pretty=True)
    try:
        build_logos()
    except Exception as e:
        warn(f"Логотипы: {e!r}")
    try:
        build_positions()
    except Exception as e:
        warn(f"Позиции ProRoles: {e!r}")
    # «Будущие игры» на страницах команд — на каждом сборе (страницы целиком пересобираются раз в 3–12 ч, а расписание меняется чаще)
    # и сверка со свежими карточками матчей: состав, трофеи, MVP и мажоры у команды должны совпадать с тем, что в матче
    PFIELDS = ("mvp", "mvp_events", "majors", "major_wins", "major_events", "trophies", "event_wins", "name_ru", "tags")
    by_team: dict = {}
    for mf in (OUT / "match").glob("*.json"):
        md = load_json(mf, {}) or {}
        for t in md.get("teams") or []:
            if t.get("name"):
                by_team[logo_key(t["name"])] = t
    n_up = n_sync = 0
    for f in (OUT / "team").glob("*.json"):
        d = load_json(f, None)
        if not d or not d.get("name"):
            continue
        try:
            bid = team_by_name(d["name"])
            if bid:
                d["upcoming"] = bo3_upcoming(bid)
                n_up += 1
        except Exception as e:
            warn(f"Будущие игры {d.get('name')}: {e!r}")
        mt = by_team.get(logo_key(d["name"]))
        if mt and mt.get("players"):
            mp = {norm(p.get("nick")): p for p in mt["players"]}
            if not d.get("roster_src") == "lp" and mt.get("players"):
                # у матча состав точнее (Liquipedia + расписание): берём его, со всеми полями
                d["roster"] = [dict(p) for p in mt["players"]]
                d["coach"] = mt.get("coach") or d.get("coach") or []
            else:
                for p in d.get("roster") or []:
                    q = mp.get(norm(p.get("nick")))
                    if q:
                        for k in PFIELDS:
                            if q.get(k) and not p.get(k):
                                p[k] = q[k]
            if mt.get("prizes") and not d.get("prizes"):
                d["prizes"] = mt["prizes"]
            n_sync += 1
        # MVP и мажоры из памяти HLTV — без новых запросов
        for p in (d.get("roster") or []) + (d.get("coach") or []):
            pl = hltv_player_ids.get(norm(p.get("nick")))
            c = pl and (hltv_cache.get(f"pinfo5:{pl['id']}") or hltv_cache.get(f"pinfo4:{pl['id']}"))
            if c and c.get("data"):
                for k, v in c["data"].items():
                    if v and not p.get(k):
                        p[k] = v
        save_json(f, d)
    print(f"Будущие игры обновлены у {n_up} команд, сверено с матчами: {n_sync}")
    for f in list((OUT / "team").glob("*.json")) + list((OUT / "match").glob("*.json")):  # ручные имена — и в старых файлах
        d = load_json(f, None)
        if d is not None and NAME_RU:
            before = json.dumps(d, ensure_ascii=False)
            apply_name_ru(d)
            if json.dumps(d, ensure_ascii=False) != before:
                save_json(f, d)
    print(f"Готово: {len(listing)} матчей, PandaScore: {stats['panda_requests']}, bo3: {stats['bo3_requests']}, "
          f"переводов: {stats['translations']}, предупреждений: {len(stats['warnings'])}")
    return 0


def news_main() -> int:
    """Каждые ~20 минут: свежие посты каналов → лента и новости уже собранных матчей. Статистику не трогаем."""
    L = load_json(OUT / "matches.json", {}) or {}
    CURRENT_TOURS[:] = list(dict.fromkeys(m.get("tournament") for m in L.get("matches") or [] if m.get("tournament")))
    try:
        save_json(OUT / "feed.json", build_feed())
    except Exception as e:
        warn(f"Лента: {e!r}")
    for f in (OUT / "match").glob("*.json"):
        d = load_json(f, None)
        if not d or not d.get("teams"):
            continue
        try:
            d["news"] = build_news(d["teams"])
            save_json(f, d)
        except Exception as e:
            warn(f"Новости {f.stem}: {e!r}")
    try:
        d2_sync()  # каждые 5 минут: вето перед матчем и время начала
    except Exception as e:
        warn(f"dust2 матчи: {e!r}")
    save_json(TG_CACHE_F, tg_cache)
    save_json(GIGA_USAGE_F, giga_usage)
    save_json(DEDUP_CACHE_F, dedup_cache)
    save_json(TR_CACHE_F, tr_cache)
    print(f"Новости обновлены, предупреждений: {len(stats['warnings'])}")
    return 0


if __name__ == "__main__":
    sys.exit(news_main() if FAST else main())
