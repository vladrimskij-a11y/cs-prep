#!/usr/bin/env python3
"""
Сборщик данных для CS Prep (Telegram Mini App для комментатора).

Что делает за один запуск:
  1. Берёт ближайшие матчи CS2 тир-1 (S и A) из PandaScore.
  2. Для матчей в ближайшие DETAIL_HOURS часов собирает:
     составы, форму команд, личные встречи, статистику по картам.
  3. Ищет новости про игроков и команды за NEWS_DAYS дней
     (Google News на русском и английском + RSS профильных сайтов),
     нейросеть отсеивает лишнее и переводит на русский.
  4. Складывает всё в docs/data/*.json — их читает Mini App.

Работает только на стандартной библиотеке Python, ничего ставить не нужно.

Переменные окружения:
  PANDASCORE_TOKEN  — токен PandaScore (обязателен)
  GITHUB_TOKEN      — токен для нейросети GitHub Models (в GitHub Actions есть сам)
  AI_MODEL          — модель, по умолчанию openai/gpt-4.1-mini
  TIERS             — уровни турниров, по умолчанию "s,a"
  DAYS_AHEAD        — на сколько дней вперёд показывать матчи (7)
  DETAIL_HOURS      — для матчей в ближайшие N часов собирать подробности (72)
  NEWS_DAYS         — глубина новостей в днях (7)
"""

from __future__ import annotations

import email.utils
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
TOKEN = os.environ.get("PANDASCORE_TOKEN", "").strip()
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-4.1-mini")
AI_URL = "https://models.github.ai/inference/chat/completions"
TIERS = {t.strip().lower() for t in os.environ.get("TIERS", "s,a").split(",") if t.strip()}
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "7"))
DETAIL_HOURS = int(os.environ.get("DETAIL_HOURS", "72"))
NEWS_DAYS = int(os.environ.get("NEWS_DAYS", "7"))

# Ленты профильных сайтов. Любую можно убрать или добавить свою.
RSS_FEEDS = [
    ("HLTV", "https://www.hltv.org/rss/news"),
    ("Cybersport.ru", "https://www.cybersport.ru/rss/materials"),
]

UA = "Mozilla/5.0 (compatible; cs-prep-bot/1.0; +https://github.com)"
NOW = datetime.now(timezone.utc)

stats = {"panda_requests": 0, "ai_calls": 0, "warnings": []}


def warn(msg: str) -> None:
    print("WARN:", msg, file=sys.stderr)
    stats["warnings"].append(msg)


# ---------------------------------------------------------------- HTTP

def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                last = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
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


# ---------------------------------------------------------------- cache

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(data, ensure_ascii=False, indent=1 if pretty else None, separators=None if pretty else (",", ":"))
    path.write_text(txt, "utf-8")


GAMES_CACHE_F = CACHE / "games.json"      # map info per past match id (never changes)
AI_CACHE_F = CACHE / "news_ai.json"       # AI verdicts per news item
games_cache: dict = load_json(GAMES_CACHE_F, {})
ai_cache: dict = load_json(AI_CACHE_F, {})
maps_endpoint_ok = True


# ---------------------------------------------------------------- helpers

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def team_brief(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "acronym": t.get("acronym"),
        "image": t.get("image_url"),
        "location": t.get("location"),
    }


def tournament_label(m: dict) -> str:
    league = (m.get("league") or {}).get("name") or ""
    serie = (m.get("serie") or {}).get("full_name") or (m.get("serie") or {}).get("name") or ""
    stage = (m.get("tournament") or {}).get("name") or ""
    parts = [league, serie]
    label = " ".join(p for p in parts if p).strip()
    return f"{label} · {stage}" if stage else label


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
        "league": (m.get("league") or {}).get("name"),
        "league_image": (m.get("league") or {}).get("image_url"),
        "tier": ((m.get("tournament") or {}).get("tier") or "").lower(),
        "teams": opps,
        "stream": (ru or main or {}).get("raw_url"),
    }


# ---------------------------------------------------------------- matches

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
_past_cache: dict[int, list] = {}


def fetch_team(team_id: int) -> dict:
    if team_id not in _team_cache:
        try:
            _team_cache[team_id] = panda(f"/teams/{team_id}")
        except Exception as e:
            warn(f"Команда {team_id}: {e}")
            _team_cache[team_id] = {}
    return _team_cache[team_id]


def fetch_past(team_id: int, pages: int = 2) -> list[dict]:
    if team_id in _past_cache:
        return _past_cache[team_id]
    res: list[dict] = []
    for page in range(1, pages + 1):
        try:
            batch = panda("/csgo/matches/past", {
                "filter[opponent_id]": team_id, "sort": "-begin_at", "per_page": 100, "page": page})
        except PandaForbidden as e:
            warn(f"История матчей недоступна на этом тарифе: {e}")
            break
        except Exception as e:
            warn(f"История команды {team_id}: {e}")
            break
        res.extend(batch)
        if len(batch) < 100:
            break
    res = [m for m in res if m.get("status") == "finished" and not m.get("forfeit")]
    _past_cache[team_id] = res
    return res


def map_name_from_game(g: dict) -> str | None:
    mp = g.get("map")
    if isinstance(mp, dict):
        return mp.get("name")
    if isinstance(mp, str):
        return mp
    return None


def games_with_maps(m: dict, allow_fetch: bool) -> list[dict]:
    """Список карт матча: [{'map': 'Mirage', 'winner_id': 123}]."""
    global maps_endpoint_ok
    key = str(m["id"])
    if key in games_cache:
        return games_cache[key]
    games = sorted(m.get("games") or [], key=lambda g: g.get("position") or 0)
    res = []
    for g in games:
        if g.get("status") != "finished":
            continue
        res.append({"map": map_name_from_game(g), "winner_id": (g.get("winner") or {}).get("id")})
    if res and all(r["map"] for r in res):
        games_cache[key] = res
        return res
    if allow_fetch and maps_endpoint_ok:
        try:
            full = panda(f"/csgo/matches/{m['id']}/games")
            res = []
            for g in sorted(full, key=lambda g: g.get("position") or 0):
                if g.get("status") != "finished":
                    continue
                res.append({"map": map_name_from_game(g), "winner_id": (g.get("winner") or {}).get("id")})
            games_cache[key] = res
            return res
        except PandaForbidden:
            maps_endpoint_ok = False
            warn("Карты по матчам недоступны на бесплатном тарифе PandaScore — нужен другой источник")
        except Exception as e:
            warn(f"Карты матча {m['id']}: {e}")
    return res


def score_of(m: dict, team_id: int) -> int | None:
    for r in m.get("results") or []:
        if r.get("team_id") == team_id:
            return r.get("score")
    return None


def other_team(m: dict, team_id: int) -> dict:
    for o in m.get("opponents") or []:
        t = o.get("opponent") or {}
        if t.get("id") != team_id:
            return t
    return {}


def build_form(team_id: int, past: list[dict], n: int = 10) -> list[dict]:
    res = []
    for m in past[:n]:
        opp = other_team(m, team_id)
        res.append({
            "date": m.get("begin_at"),
            "opponent": opp.get("acronym") or opp.get("name"),
            "opponent_name": opp.get("name"),
            "score": score_of(m, team_id),
            "opp_score": score_of(m, opp.get("id")),
            "won": m.get("winner_id") == team_id,
            "tournament": tournament_label(m),
            "tier": ((m.get("tournament") or {}).get("tier") or "").lower(),
        })
    return res


def build_h2h(a: int, b: int, past_a: list[dict]) -> list[dict]:
    res = []
    for m in past_a:
        ids = {(o.get("opponent") or {}).get("id") for o in m.get("opponents") or []}
        if b not in ids:
            continue
        maps = games_with_maps(m, allow_fetch=True)
        res.append({
            "date": m.get("begin_at"),
            "tournament": tournament_label(m),
            "bo": m.get("number_of_games"),
            "score": [score_of(m, a), score_of(m, b)],
            "winner": 0 if m.get("winner_id") == a else 1 if m.get("winner_id") == b else None,
            "maps": [{"map": g["map"], "winner": 0 if g["winner_id"] == a else 1 if g["winner_id"] == b else None}
                     for g in maps],
        })
    return res


def build_maps(team_ids: list[int], pasts: list[list[dict]], window_days: int = 120, max_matches: int = 30) -> dict:
    """Статистика по картам за последние window_days дней."""
    since = NOW - timedelta(days=window_days)
    table: dict[str, dict] = {}
    recent: dict[str, list] = {}
    any_maps = False
    for idx, (tid, past) in enumerate(zip(team_ids, pasts)):
        for m in past[:max_matches]:
            d = parse_iso(m.get("begin_at"))
            if d and d < since:
                break
            opp = other_team(m, tid)
            for g in games_with_maps(m, allow_fetch=True):
                name = g.get("map")
                if not name:
                    continue
                any_maps = True
                row = table.setdefault(name, {"map": name, "teams": [{"played": 0, "won": 0}, {"played": 0, "won": 0}]})
                row["teams"][idx]["played"] += 1
                won = g.get("winner_id") == tid
                row["teams"][idx]["won"] += int(won)
                lst = recent.setdefault(name, [])
                lst.append({"team": idx, "date": m.get("begin_at"), "vs": opp.get("acronym") or opp.get("name"),
                            "won": won, "tournament": tournament_label(m)})
    for name in recent:
        recent[name].sort(key=lambda r: r["date"] or "", reverse=True)
    rows = sorted(table.values(), key=lambda r: -(r["teams"][0]["played"] + r["teams"][1]["played"]))
    return {"available": any_maps, "window_days": window_days, "table": rows, "recent": recent}


def player_brief(p: dict) -> dict:
    age = p.get("age")
    if not age and p.get("birthday"):
        bd = parse_iso(p["birthday"] + "T00:00:00Z") if len(p["birthday"]) == 10 else parse_iso(p["birthday"])
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


# ---------------------------------------------------------------- news

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
            title = title[: -len(source) - 3]
        items.append({
            "title": title,
            "url": link,
            "source": source,
            "date": parse_date(it.findtext("pubDate")),
            "snippet": strip_html(it.findtext("description") or "")[:300],
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
    time.sleep(0.5)
    return items


def google_news(query: str, lang: str) -> list[dict]:
    q = f"{query} when:{NEWS_DAYS}d"
    if lang == "ru":
        params = {"q": q, "hl": "ru", "gl": "RU", "ceid": "RU:ru"}
    else:
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    return fetch_feed(url, "Google News")


def word_re(word: str) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(word) + r"(?![\w])", re.IGNORECASE)


def item_key(it: dict) -> str:
    norm = re.sub(r"\W+", "", it["title"].lower())[:120]
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def gather_candidates(teams: list[dict]) -> list[dict]:
    """Собирает новости-кандидаты про игроков и команды матча."""
    found: dict[str, dict] = {}

    def add(it: dict, about: str):
        k = item_key(it)
        cur = found.get(k)
        if cur is None:
            cur = dict(it)
            cur["key"] = k
            cur["about"] = []
            found[k] = cur
        if about not in cur["about"]:
            cur["about"].append(about)

    cs_ctx = '(CS2 OR "Counter-Strike" OR "КС2" OR "CS 2")'
    for t in teams:
        tname = t.get("name") or ""
        for lang in ("ru", "en"):
            for it in google_news(f'"{tname}" {cs_ctx}', lang):
                add(it, tname)
        for p in t.get("players") or []:
            nick = p.get("nick") or ""
            if len(nick) < 2:
                continue
            for lang in ("ru", "en"):
                for it in google_news(f'"{nick}" {cs_ctx}', lang):
                    add(it, nick)

    # профильные сайты: совпадение по нику или названию команды
    patterns = []
    for t in teams:
        if t.get("name"):
            patterns.append((t["name"], word_re(t["name"])))
        for p in t.get("players") or []:
            if p.get("nick") and len(p["nick"]) >= 3:
                patterns.append((p["nick"], word_re(p["nick"])))
    for source, url in RSS_FEEDS:
        for it in fetch_feed(url, source):
            text = f"{it['title']} {it['snippet']}"
            for label, rx in patterns:
                if rx.search(text):
                    add(it, label)
    res = sorted(found.values(), key=lambda i: i["date"], reverse=True)
    return res


def ai_chat(system: str, user: str) -> dict | None:
    if not GH_TOKEN:
        return None
    body = json.dumps({
        "model": AI_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(AI_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    for attempt in range(3):
        try:
            stats["ai_calls"] += 1
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "replace")
            if e.code == 429 and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            warn(f"Нейросеть: HTTP {e.code} {msg}")
            return None
        except Exception as e:
            warn(f"Нейросеть: {e}")
            return None
    return None


AI_SYSTEM = """Ты помогаешь русскоязычному комментатору CS2 готовиться к трансляции.
Тебе дают список игроков/команд и заголовки новостей за последние дни.
Для КАЖДОЙ новости реши, действительно ли она про указанного игрока/команду из мира CS2
(ники бывают обычными словами — отсекай совпадения не про киберспорт и не про этих людей).
Для релевантных:
 - title_ru: заголовок на русском (если уже на русском — оставь как есть, поправь только явный мусор),
 - summary_ru: 1 короткое предложение по-русски, что из этого полезно комментатору (без выдумок, только из заголовка/описания),
 - kind: одно из "интервью", "заявление", "соцсети", "трансфер", "результат", "слухи", "другое",
 - about: список ников/команд из входа, о ком новость.
Верни JSON: {"items":[{"key":"...","relevant":true|false,"title_ru":"...","summary_ru":"...","kind":"...","about":["..."]}]}
Для нерелевантных достаточно {"key":"...","relevant":false}."""


def ai_process(candidates: list[dict], teams: list[dict]) -> bool:
    """Заполняет ai-поля у кандидатов, используя кэш. Возвращает True, если нейросеть отработала."""
    todo = [c for c in candidates if c["key"] not in ai_cache]
    if not GH_TOKEN:
        return False
    roster = []
    for t in teams:
        nicks = ", ".join(p.get("nick") or "" for p in t.get("players") or [])
        roster.append(f"{t.get('name')}: {nicks}")
    ok = True
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        lines = []
        for c in chunk:
            d = c["date"].strftime("%d.%m") if c.get("date") else ""
            snip = f" | {c['snippet'][:160]}" if c.get("snippet") and c["snippet"] not in c["title"] else ""
            lines.append(f"key={c['key']} | {d} | {c['source']} | про: {', '.join(c['about'])} | {c['title']}{snip}")
        user = "Составы:\n" + "\n".join(roster) + "\n\nНовости:\n" + "\n".join(lines)
        res = ai_chat(AI_SYSTEM, user)
        if not res:
            ok = False
            break
        for r in res.get("items") or []:
            k = r.get("key")
            if k:
                r["t"] = iso(NOW)
                ai_cache[k] = r
        time.sleep(2)
    return ok


def build_news(teams: list[dict]) -> dict:
    cands = gather_candidates(teams)
    ai_ok = ai_process(cands, teams)
    items = []
    for c in cands:
        v = ai_cache.get(c["key"])
        if v is not None and not v.get("relevant"):
            continue
        items.append({
            "title": c["title"],
            "title_ru": (v or {}).get("title_ru"),
            "summary_ru": (v or {}).get("summary_ru"),
            "kind": (v or {}).get("kind"),
            "about": (v or {}).get("about") or c["about"],
            "source": c["source"],
            "url": c["url"],
            "date": iso(c["date"]) if c.get("date") else None,
            "checked": v is not None,
        })
    return {"updated": iso(NOW), "ai": ai_ok and bool(GH_TOKEN), "days": NEWS_DAYS, "items": items[:80]}


# ---------------------------------------------------------------- main

def build_detail(m: dict, prev: dict | None) -> dict:
    brief = match_brief(m)
    team_ids = [t["id"] for t in brief["teams"]]
    teams = []
    pasts = []
    for t in brief["teams"]:
        full = fetch_team(t["id"])
        past = fetch_past(t["id"])
        pasts.append(past)
        players = [player_brief(p) for p in full.get("players") or [] if p.get("active", True)]
        teams.append({**t, "players": players, "form": build_form(t["id"], past)})
    detail = {**brief, "teams": teams, "updated": iso(NOW)}
    if len(team_ids) == 2:
        detail["h2h"] = build_h2h(team_ids[0], team_ids[1], pasts[0])
        detail["maps"] = build_maps(team_ids, pasts)
    begin = parse_iso(brief["begin_at"])
    if begin and begin - NOW <= timedelta(hours=48) or not (prev and prev.get("news")):
        detail["news"] = build_news(teams)
    else:
        detail["news"] = prev["news"]
    return detail


def prune_caches() -> None:
    cutoff = iso(NOW - timedelta(days=NEWS_DAYS + 7))
    for k in [k for k, v in ai_cache.items() if (v.get("t") or "") < cutoff]:
        del ai_cache[k]


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
                warn(f"Матч {m['id']}: {e}")
                detailed = path.exists()
                if detailed:
                    keep_ids.add(m["id"])
        b["has_detail"] = detailed
        listing.append(b)

    # удаляем старые карточки
    for f in (OUT / "match").glob("*.json"):
        if int(f.stem) not in keep_ids:
            f.unlink()

    prune_caches()
    save_json(GAMES_CACHE_F, games_cache)
    save_json(AI_CACHE_F, ai_cache)
    save_json(OUT / "matches.json", {
        "updated": iso(NOW),
        "tiers": sorted(TIERS),
        "matches": listing,
        "meta": {"maps_available": maps_endpoint_ok, **stats},
    }, pretty=True)
    print(f"Готово: {len(listing)} матчей, запросов PandaScore: {stats['panda_requests']}, "
          f"вызовов нейросети: {stats['ai_calls']}, предупреждений: {len(stats['warnings'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
