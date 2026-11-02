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


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        txt = json.dumps(data, ensure_ascii=False, indent=1)
    else:
        txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(txt, "utf-8")


GAMES_CACHE_F = CACHE / "bo3_games_v2.json"   # карты по завершённым матчам bo3, как отдаёт bo3 (не меняются)
DETAILS_CACHE_F = CACHE / "bo3_game_details_v2.json"  # раунды и статистика игроков по каждой карте
TEAMS_CACHE_F = CACHE / "bo3_teams.json"     # PandaScore id → bo3 id
TR_CACHE_F = CACHE / "translations.json"     # переводы заголовков
ROSTER_CACHE_F = CACHE / "liquipedia_rosters_v3.json"  # тренер, роли, даты рождения с Liquipedia
PRON_CACHE_F = CACHE / "pronunciation_v2.json"       # имена и ники по-русски с ударением
games_cache: dict = load_json(GAMES_CACHE_F, {})
details_cache: dict = load_json(DETAILS_CACHE_F, {})
DETAILS_NEW_LIMIT = int(os.environ.get("DETAILS_NEW_LIMIT", "450"))  # новых карт с раундами за запуск
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


def fetch_future_tournaments() -> list[dict]:
    """Идущие и будущие турниры тир-1 на TOURS_MONTHS_AHEAD месяцев вперёд, сгруппированные по серии (событию)."""
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
                                      "prize": t.get("prizepool"), "matches": 0, "next": None, "live": 0})
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
    wait = 2.5 - (time.time() - _lp_last[0])
    if wait > 0:
        time.sleep(wait)
    _lp_last[0] = time.time()
    stats["liquipedia_requests"] += 1
    url = LP_API + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "titles": "|".join(titles), "redirects": 1, "format": "json"})
    try:
        data = json.loads(http_get(url, {"User-Agent": LP_UA, "Accept": "application/json"}))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _lp_blocked[0] = True  # больше не стучимся до следующего запуска
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
            add_alias(found, pick.get("name"), pick.get("slug"), pick.get("acronym"))
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
    """Карты матча как есть: [{'map','n','wn','ln','ws','ls'}] (кто выиграл — по клан-тегу). Кэш навсегда."""
    key = str(m["id"])
    if key in games_cache:
        return games_cache[key]
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
            "opponent_name": opp.get("name"),
            "score": my, "opp_score": their,
            "won": m.get("winner_team_id") == bid,
            "tournament": tour_name(m),
            "tier": (m.get("tier") or "").lower(),
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


def build_h2h(a: int, b: int, hist_a: list[dict]) -> list[dict]:
    since = NOW - timedelta(days=H2H_DAYS)
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
        maps = [{"map": r["map"], "winner": None if r["won"] is None else (0 if r["won"] else 1), "score": r["score"]}
                for r in map_results(m, a)]
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
            opp = opp_of(m, bid)
            for r in map_results(m, bid):
                name = r.get("map")
                if not name or r["won"] is None:
                    continue
                row = table.setdefault(name, {"map": name, "teams": [{"played": 0, "won": 0}, {"played": 0, "won": 0}]})
                row["teams"][idx]["played"] += 1
                row["teams"][idx]["won"] += int(r["won"])
                recent.setdefault(name, []).append({
                    "team": idx, "date": m.get("start_date"), "vs": opp.get("acronym") or opp.get("name"),
                    "won": r["won"], "score": r["score"], "tournament": tour_name(m)})
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
    if todo and GIGA_KEY and not _giga["off"]:
        def line(p, full):
            fn, ln = p.get("first_name"), p.get("last_name")
            who = f"имя: {fn}; фамилия: {ln}" if fn and ln else f"полное имя: {full or 'неизвестно'}"
            return f"- ник: {p.get('nick')}; {who}; страна: {p.get('nationality') or p.get('flag') or '?'}"
        lines = "\n".join(line(p, full) for p, full in todo)
        res = parse_json_loose(giga_chat(PRON_SYSTEM, lines, 1200, model=os.environ.get("GIGACHAT_PRON_MODEL", "GigaChat-2-Max")))
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
            tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.DOTALL)
            text = ""
            if tx:
                text = re.sub(r"<br\s*/?>", " ZZBRZZ ", tx.group(1))
                text = strip_html(text).replace(" ZZBRZZ ", "\n").replace("ZZBRZZ", "\n").strip()
            photos = re.findall(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
            vthumb = re.findall(r"tgme_widget_message_video_thumb[^>]*background-image:url\('([^']+)'\)", b)
            d = parse_iso(tm.group(1))
            page.append({"channel": ch, "id": int(pm.group(2)), "date": d, "text": text,
                         "photo": (photos or vthumb or [None])[0], "url": f"https://t.me/{ch}/{pm.group(2)}"})
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
    if not GIGA_KEY or _giga["off"] or not giga_budget_ok("max"):
        return None
    try:
        img = http_get(url, {"Accept": "image/*"}, timeout=25)
    except Exception:
        return None
    if len(img) > 9_000_000:
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


TG_SYSTEM = """Ты редактор новостей о Counter-Strike 2. Тебе дают пост из Telegram-канала (текст и, если был, текст с картинки).
Определи, новость ли это про киберспорт CS2 (не реклама, не ставки/промокоды/казино, не розыгрыш, не мем без новости, не другая игра,
не расписание и не анонс/напоминание о матчах — «сегодня играют…», «когда матчи», «стартует турнир», превью и прогнозы — это НЕ новость;
не ход матча и не моменты: клатчи, квадро, эйсы, хайлайты, клипы, «результат первой карты», счёт карт и раундов, голый итог матча
без цитат и подробностей — это НЕ новость).
Новость — это статья, интервью, цитата, заявление, трансфер/замена/аренда, слух, интересный факт или особенность об игроке или команде.
Если да — дай короткий заголовок по-русски (до 100 символов), ники и названия команд латиницей как в оригинале.
Ответ строго JSON: {"news": true|false, "title": "<заголовок>"}"""


def tg_process(post: dict) -> dict:
    key = f"{post['channel']}/{post['id']}"
    c = tg_cache.get(key)
    if c is not None:
        return c
    text = post["text"] or ""
    info: dict = {"t": iso(NOW)}
    ad = re.search(r"промокод|реклама|#ad\b|erid|казино|casino|ставк\w*|букмекер|розыгрыш|giveaway|подпишись", text, re.IGNORECASE) \
        or SPAM.search(text[:200])
    if post.get("photo") and len(text) < 60 and _tg_giga[0] < TG_GIGA_LIMIT:
        _tg_giga[0] += 1
        info["ocr"] = giga_read_image(post["photo"])
    full = (text + "\n" + (info.get("ocr") or "")).strip()
    if not full:
        info["news"] = False
    elif _tg_giga[0] < TG_GIGA_LIMIT and GIGA_KEY and not _giga["off"]:
        _tg_giga[0] += 1
        res = parse_json_loose(giga_chat(TG_SYSTEM, full[:2500], 200))
        if res is not None:
            info["news"] = bool(res.get("news")) and not ad
            info["title"] = (res.get("title") or "").strip() or None
    if "news" not in info:  # нейросеть недоступна — простое правило
        info["news"] = not ad and len(full) >= 40
        info["fallback"] = True
    if not info.get("title"):
        first = re.split(r"(?<=[.!?…])\s+|\n", full.strip(), 1)[0] if full else ""
        info["title"] = first[:120]
    if "fallback" not in info:
        tg_cache[key] = info
    return info


_tg_all: list[dict] | None = None
_tg_titles: dict = {}


def tg_posts() -> list[dict]:
    """Все посты новостных каналов за неделю, уже отобранные и с заголовками (один раз за запуск)."""
    global _tg_all
    if _tg_all is not None:
        return _tg_all
    res = []
    for ch in TG_NEWS_CHANNELS:
        title, posts = tg_fetch_channel(ch)
        _tg_titles[ch] = title
        for p in posts:
            info = tg_process(p)
            if not info.get("news") or SPAM.search(info.get("title") or "") or SPAM.search((p["text"] or "")[:200]):
                continue
            if is_live_noise((info.get("title") or "") + "\n" + (p["text"] or "") + "\n" + (info.get("ocr") or "")):
                continue  # клатчи, квадро, счёт карт — трансляция, а не новость
            body = p["text"] or info.get("ocr") or ""
            res.append({
                "title": info["title"], "url": p["url"], "source": f"TG · {title}", "channel": ch,
                "date": p["date"], "snippet": body[:400], "text": body, "photo": p.get("photo"), "tg": True,
            })
    res.sort(key=lambda x: x["date"], reverse=True)
    _tg_all = res
    return res


def tg_text_preview(text: str, limit: int = 420) -> str:
    t = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def build_feed() -> dict:
    """Лента на главном экране: посты каналов без повторов."""
    posts = [dict(p, about=["CS"]) for p in tg_posts()]
    for p in posts:
        p["key"] = "tg:" + p["url"].split("t.me/")[1]
        p["title_ru0"], p["lang0"] = None, "ru"
    clusters = dedupe_news(posts, [], same_who_required=False)
    items = []
    for cl in clusters[:150]:
        it, others = cl[0], cl[1:]
        items.append({
            "key": it["key"], "title": it["title"], "text": tg_text_preview(it["text"]), "full": tg_text_preview(it["text"], 3000),
            "photo": it.get("photo"),
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


def hltv_get(path: str) -> str | None:
    if path in _hltv_pages:
        return _hltv_pages[path]
    if _hltv_blocked[0]:
        return None
    wait = 2.5 - (time.time() - _hltv_last[0])
    if wait > 0:
        time.sleep(wait)
    _hltv_last[0] = time.time()
    stats["hltv_requests"] = stats.get("hltv_requests", 0) + 1
    try:
        html = http_get(HLTV + path, {"User-Agent": HLTV_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                        timeout=30).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            _hltv_blocked[0] = True  # HLTV просит остановиться — до следующего запуска не обращаемся
        warn(f"HLTV {path}: {e!r}")
        html = None
    except Exception as e:
        warn(f"HLTV {path}: {e!r}")
        html = None
    _hltv_pages[path] = html
    return html


def hclean(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<img[^>]*>|<svg.*?</svg>|<source[^>]*>", "", h, flags=re.S))


def hltv_events() -> dict:
    """slug → путь страницы события."""
    h = hltv_get("/events") or ""
    out = {}
    for href, slug in re.findall(r'href="(/events/\d+/([^"#?]+))"', h):
        out.setdefault(slug, href)
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
    toks = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in ("the",)]
    toks = ["iem" if t == "intel" else t for t in toks if t not in ("extreme", "masters")] if "intel" in toks else toks
    best = None
    for slug, href in events.items():
        st = slug.split("-")
        if all(t in st for t in toks) and (best is None or len(slug) < len(best[0])):
            best = (slug, href)
    return best[1] if best else None


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


def tournament_extras(name: str) -> dict | None:
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


# ---------------------------------------------------------------- вето (пики и баны) с HLTV

VETO_CACHE_F = CACHE / "hltv_vetoes.json"
veto_cache: dict = load_json(VETO_CACHE_F, {})   # "match:<id>" → вето навсегда; "results:<team>" → список матчей на 12 ч
VETO_NEW_LIMIT = int(os.environ.get("VETO_NEW_LIMIT", "25"))  # вето берём с dust2.us (сайт-партнёр HLTV, та же база)
D2 = "https://www.dust2.us"
_d2_blocked = [False]
_d2_last = [0.0]


def d2_get(path: str) -> str | None:
    if _d2_blocked[0]:
        return None
    wait = 3.0 - (time.time() - _d2_last[0])
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
VETO_MATCHES = 20
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
    key = f"results:{team['id']}"
    c = veto_cache.get(key)
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=12):
        return c["data"]
    h = d2_get(f"/results?team={team['id']}")
    if not h:
        return c["data"] if c else []
    out = []
    for mid, slug in re.findall(r'href="/matches/(\d+)/([^"#?]+)"', h):
        if mid not in [x["id"] for x in out]:
            out.append({"id": mid, "slug": slug})
        if len(out) >= VETO_MATCHES:
            break
    veto_cache[key] = {"t": iso(NOW), "data": out}
    return out


def hltv_veto(match: dict) -> dict | None:
    """Вето матча со страницы dust2.us: по порядку карты BANNED / PICKED / оставшаяся. Хранится навсегда."""
    key = f"match:{match['id']}"
    if key in veto_cache:
        return veto_cache[key]
    if _veto_new[0] >= VETO_NEW_LIMIT:
        return None
    _veto_new[0] += 1
    h = d2_get(f"/matches/{match['id']}/{match['slug']}")
    if not h:
        return None
    b = re.sub(r"\s+", " ", h)
    steps = []
    i = b.find('class="maps-container"')
    sec = b[i:i + 40000] if i >= 0 else ""
    for n, box in enumerate(re.split(r'<div class="standard-box map-container ', sec)[1:], 1):
        nm = re.search(r'map-container-map-name">([^<]+)<', box)
        tp = re.search(r'map-container-type">([^<]+)<', box)
        team = re.search(r'class="team-logo[^"]*" title="([^"]+)"', box)
        if not nm:
            continue
        t = (tp.group(1).strip().upper() if tp else "")
        act = "ban" if "BAN" in t else "pick" if "PICK" in t else "decider"
        steps.append({"n": n, "team": strip_html(team.group(1)) if team and act != "decider" else None, "act": act,
                      "map": strip_html(nm.group(1))})
    dm = re.search(r'data-unix="(\d+)"', b)
    d = {"date": iso(datetime.fromtimestamp(int(dm.group(1)) / 1000, tz=timezone.utc)) if dm else None, "steps": steps}
    if steps:
        veto_cache[key] = d   # вето сыгранного матча не меняется
    return d


def build_veto(teams: list[dict]) -> dict | None:
    """По каждой команде: что пикает, что банит первым и вообще, по последним матчам; плюс вероятный маппул."""
    res = []
    for t in teams:
        ht = hltv_team_for(t)
        if not ht:
            res.append(None)
            continue
        picks, bans, first_bans, n, deciders = {}, {}, {}, 0, {}
        for mt in hltv_team_matches(ht):
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
            for s in v["steps"]:
                if s["act"] == "decider":
                    deciders[s["map"]] = deciders.get(s["map"], 0) + 1
            for i, s in enumerate(mine):
                tgt = picks if s["act"] == "pick" else bans
                tgt[s["map"]] = tgt.get(s["map"], 0) + 1
                if s["act"] == "ban" and i == 0:
                    first_bans[s["map"]] = first_bans.get(s["map"], 0) + 1
        top = lambda d: sorted(d.items(), key=lambda x: -x[1])
        res.append({"team": ht["name"], "matches": n, "picks": top(picks), "bans": top(bans), "first_bans": top(first_bans),
                    "deciders": top(deciders)})
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
    r"клатч\w*|clutch|квадр\w*|\bквад\w*|\bэйс\w*|\bace\b|\b[345]k\b|\b1 ?(?:в|v|vs|на) ?[2-5]\b|хайлайт\w*|highlight|"
    r"\bмомент\w*|\bклип\w*|\bclip\b|прямо сейчас|\blive\b|\bлайв\b|идёт матч|идет матч|"
    r"(?:перв|втор|трет)\w+ карт\w*|\bкарт[ау] (?:за|в пользу)|забира\w+ (?:карту|mirage|inferno|nuke|ancient|anubis|dust2|train|overpass|vertigo|cache)|"
    r"взял\w* (?:карту|mirage|inferno|nuke|ancient|anubis|dust2|train|overpass)|счёт по картам|\bmap ?[123]\b|"
    r"\b\d{1,2} ?[:\-–] ?\d{1,2}\b.{0,20}(?:раунд|по раундам|на карте)|по раундам|пистолетк\w+ раунд",
    re.IGNORECASE)
RESULT_ONLY = re.compile(r"(?:обыгра\w+|победил\w*|выигра\w+|проигра\w+|уступил\w*|разгроми\w+|вылете\w+)\b.{0,60}\b[0-3] ?[:\-–] ?[0-3]\b",
                         re.IGNORECASE)
HAS_SUBSTANCE = re.compile(r"[«\"“].{8,}[»\"”]|интервью|заяв\w+|рассказ\w+|признал\w*|ответил\w*|высказал\w*|трансфер\w*|"
                           r"подписал\w*|покинул\w*|замен\w+|скамейк\w+|аренд\w+|контракт\w*|слух\w*|инсайд\w*|травм\w*",
                           re.IGNORECASE)


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
GIGA_DAY_LIMIT = {"lite": int(os.environ.get("GIGA_DAY_LITE", "500000")), "max": int(os.environ.get("GIGA_DAY_MAX", "50000")),
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


def giga_chat(system: str, user: str, max_tokens: int = 900, model: str | None = None) -> str | None:
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
            _giga["off"] = True
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
        return {"url": it["url"], "summary_ru": tg_text_preview(it.get("text") or ""), "image": it.get("photo"), "lang": "ru"}
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
    clusters = dedupe_news(items, teams)

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
            "image": art.get("image"),
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
    return (r, 0 if it.get("lang0") == "ru" else 1, it["date"])


DEDUP_SYSTEM = """Ты редактор новостей о Counter-Strike 2. Тебе дают пронумерованный список заголовков (уже по-русски) и составы двух команд.
1) В "drop" перечисли номера заголовков, которые НЕ про киберспорт Counter-Strike и не про этих игроков/команды
   (другие игры, кино, музыка, однофамильцы, составы клубов в Dota/Valorant и т.п.), А ТАКЖЕ расписания, анонсы и превью матчей,
   страницы «матч X — Y, дата и время», «где смотреть», онлайн-результаты и прогнозы на ставки — это не новости.
2) В "groups" перечисли группы номеров, которые рассказывают ОДНУ И ТУ ЖЕ новость (одно событие), даже если формулировки и языки разные.
   Разные события про одного игрока — это разные новости, их не объединяй.
Ответ строго JSON: {"drop": [номера], "groups": [[номера], [номера]]}"""


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
    if GIGA_KEY and not _giga["off"] and len(clusters) > 1:
        roster = "\n".join(f"{t['name']}: " + ", ".join(p.get("nick") or "" for p in t.get("players") or []) for t in teams) or "(общая лента новостей CS2 — оценивай только про CS2 или нет)"
        lines = "\n".join(f"{i + 1}. [{', '.join(cl[0]['about'])}] {cl[0]['title_ru0'] or cl[0]['title']}"
                          for i, cl in enumerate(clusters))
        sig = hashlib.sha1((roster + lines).encode()).hexdigest()[:16]
        cached = dedup_cache.get(sig)
        res = cached["res"] if cached else parse_json_loose(giga_chat(DEDUP_SYSTEM, f"Составы:\n{roster}\n\nЗаголовки:\n{lines}", 600))
        if res is not None:
            dedup_cache[sig] = {"res": res, "t": iso(NOW)}
            drop = {int(x) - 1 for x in res.get("drop") or [] if str(x).isdigit()}
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

FACTS_CACHE_F = CACHE / "facts_v6.json"
facts_cache: dict = load_json(FACTS_CACHE_F, {})
FACTS_TTL_H = 6
FACTS_SYSTEM = """Ты помогаешь русскоязычному комментатору CS2 подготовиться к матчу.
Тебе дают сведения о матче (JSON): новости недели о командах и игроках, составы (имя, возраст, страна, роли), тренеры, личные встречи.
Составь до 15 фактов-сюжетов для эфира — НЕ статистику. Главное — новости недели: каждую важную новость про игроков и команды
превращай в отдельный факт (заявления, интервью, замены, аренды, переходы, слухи, конфликты, травмы, стримы).
Темы по важности: 1) сюжеты из новостей (заявления, интервью, замены, аренды, переходы); 2) люди: кто капитан, кто AWP,
кто в аренде, состав по странам, самые молодые и опытные; 3) тренеры; 4) последняя личная встреча как сюжет.
Строгие правила:
- Только то, что прямо есть в данных. Ничего не добавляй от себя: никаких титулов, рекордов, «выиграли N турниров», карьеры.
- Не считай и не выводи числа сам. Числа (возраст, счёт, даты) переписывай точно как в данных.
- Не пиши пустых фактов вроде «новостей нет».
- Одно предложение до 25 слов, по-русски, ники и названия команд латиницей как в данных.
- "tag" — одно слово: Новости, Игроки, Тренеры, Встречи.
Ответ строго JSON: {"facts": [{"tag": "...", "text": "..."}]}"""


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
        "news_week": [{"about": n.get("about"), "title": n.get("title_ru") or n.get("title"),
                       "text": (n.get("summary_ru") or "")[:300]} for n in ((d.get("news") or {}).get("items") or [])[:15]],
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
        last = h2h[0]
        if last.get("winner") in (0, 1) and last.get("score"):
            lw = nm[last["winner"]]
            out.append({"tag": "Встречи", "text": f"Последняя встреча — {last.get('tournament')} ({(last.get('date') or '')[:10]}): "
                        f"{lw} выиграли {max(last['score'])}:{min(last['score'])}."})
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
        for p in t.get("players") or []:
            tag = next((x for x in p.get("tags") or [] if str(x).startswith("В аренде")), None)
            if tag:
                out.append({"tag": "Игроки", "text": f"{p['nick']} играет за {t['name']} на правах аренды ({tag.lower()})."})
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


def build_facts(d: dict, prev: dict | None) -> list | None:
    key = str(d["id"])
    stats_facts = code_facts(d)
    c = facts_cache.get(key)
    story = None
    if c and (parse_iso(c.get("t")) or NOW) > NOW - timedelta(hours=FACTS_TTL_H):
        story = c["story"]
    elif GIGA_KEY and not _giga["off"]:
        inp = facts_input(d)
        inp = {k: inp[k] for k in ("match", "team1", "team2", "h2h_year", "news_week") if k in inp}
        for k in ("team1", "team2"):
            inp[k] = {x: inp[k][x] for x in ("name", "coach", "players") if x in inp[k]}
        data = json.dumps(inp, ensure_ascii=False)
        res = parse_json_loose(giga_chat(FACTS_SYSTEM, data[:14000], 1800, model=os.environ.get("GIGACHAT_FACTS_MODEL", "GigaChat-2-Pro")))
        allowed = _numbers(data)
        story = []
        for f in (res or {}).get("facts") or []:
            if not isinstance(f, dict) or not f.get("text"):
                continue
            if not _numbers(f["text"]) <= allowed:
                continue  # число, которого нет в данных, — модель что-то посчитала или придумала
            if not _risky_ok(f["text"], data):
                continue  # «три турнира подряд», «чемпион», «впервые» — только если это прямо есть в данных
            if re.search(r"нет новост|не поступал|не было новост", f["text"], re.IGNORECASE):
                continue
            story.append({"tag": f.get("tag") or "Новости", "text": f["text"].strip()})
        story = story[:15]
        facts_cache[key] = {"t": iso(NOW), "story": story}
    elif c:
        story = c.get("story")
    facts = (story or []) + stats_facts
    return facts[:20] or None

# ---------------------------------------------------------------- main

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
        bid = bo3_team_id({**t, "slug": full.get("slug") or t.get("slug")})
        add_alias(bid, t.get("name"), t.get("acronym"), t.get("slug"), full.get("name"), full.get("acronym"))
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
    detail["sides"] = build_sides(bids, hists)
    try:
        detail["veto"] = build_veto(teams)
    except Exception as e:
        warn(f"Вето: {e!r}")
    for t, bid, hist in zip(teams, bids, hists):
        t["stats"] = build_player_stats(t, bid, hist)
    begin = parse_iso(brief["begin_at"])
    soon = begin is not None and begin - NOW <= timedelta(hours=DETAIL_HOURS)
    if soon or not (prev and prev.get("news")):
        detail["news"] = build_news(teams)
    else:
        detail["news"] = prev["news"]
    try:
        detail["facts"] = build_facts(detail, prev)
    except Exception as e:
        warn(f"Факты: {e!r}")
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
    # id игроков HLTV из составов на страницах ближайших турниров (нужны для Rating 3.0)
    seen_tours = set()
    for m in matches:
        tname = tournament_label(m)
        if tname in seen_tours:
            continue
        seen_tours.add(tname)
        ex = tournament_extras(tname) or {}
        for lineup in (ex.get("lineups") or {}).values():
            for p in lineup:
                hltv_player_ids[norm(p["nick"])] = {"id": p["id"], "slug": p["slug"]}
        for tn, ti in (ex.get("team_ids") or {}).items():
            hltv_team_ids[norm(tn)] = {**ti, "name": tn}
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

    try:
        save_json(OUT / "feed.json", build_feed())
    except Exception as e:
        warn(f"Лента: {e!r}")
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
    future = fetch_future_tournaments()
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
    tour_list.sort(key=lambda x: (x.get("begin") or x.get("next") or "9999"))
    for tr in tour_list:
        b = parse_iso(tr.get("begin"))
        if b and b - NOW > timedelta(days=21):
            continue  # призовые и участники с HLTV — только для ближайших, чтобы лишний раз туда не ходить
        ex = tournament_extras(tr["name"])
        if ex:
            tr["hltv"] = ex
            if ex.get("prize"):
                tr["prize_usd"] = ex["prize"]
    rosters = {}
    for f in (OUT / "match").glob("*.json"):
        for t in (load_json(f, {}) or {}).get("teams") or []:
            if t.get("players"):
                rosters[norm(t["name"])] = [p["nick"] for p in t["players"]]
                if t.get("acronym"):
                    rosters[norm(t["acronym"])] = rosters[norm(t["name"])]
    try:
        major = build_major(rosters) if os.environ.get("BUILD_MAJOR") == "1" else None  # раздел скрыт по просьбе пользователя
        if major:
            save_json(OUT / "major.json", {"updated": iso(NOW), **major})
    except Exception as e:
        warn(f"Мейджор: {e!r}")
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
