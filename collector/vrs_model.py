"""Модель VRS Valve на Python — как прогноз «VRS forecast» на HLTV.

Разбор снимка Valve (live/<год>/details/<дата>/*.md) → набор данных (все матчи с победителем, LAN, весом турнира,
призовые команд) → полный пересчёт: сид по 4 факторам (bounty offered/collected, opponent network, LAN) с растяжкой
400..2000, потом Глико (RD=75) по всем матчам по времени. Перевод model/*.js из
ValveSoftware/counter-strike_regional_standings. Живой слой (Live): матчи после снимка и гарантированные призовые
идущих турниров — так HLTV считает «живой» рейтинг; прогноз — пересчёт с добавленным матчем.

Проверено 06.11.2026: снимок 02.11 воспроизводится (топ-100 ±0.5 очка), живой рейтинг HLTV на 05.11 — ±1 очко
(топ-40), 6 прогнозов HLTV (24 числа) — 20 точно, 4 с ошибкой 1.

    python collector/vrs_model.py valve/live/2026 2026_11_02            # сверка со снимком
    python collector/vrs_model.py valve/live/2026 2026_11_02 data/      # + живой рейтинг и образцы HLTV
"""
import glob, math, os, re, sys
from datetime import date, datetime, timezone

Q = math.log(10) / 400
RD = 75.0
G = 1 / math.sqrt(1 + 3 * Q * Q * RD * RD / math.pi ** 2)


def curve(x):
    return 1.0 if x >= 1 else 0.0 if x <= 0 else 1 / (1 + abs(math.log10(x)))


def glicko(r, ro, score, info=1.0):
    """Изменение очков за встречу — как glicko.js (RD постоянное, 75)."""
    ev = 1 / (1 + 10 ** (G * (r - ro) / -400))
    adj = 1 / (1 / RD ** 2 + Q * Q * G * G * ev * (1 - ev) * info * info)
    return Q * adj * G * (score - ev) * info


def _num(x):
    m = re.match(r"\s*\$?([\d,.]+)", x)
    return float(m.group(1).replace(",", "")) if m else None


def _adj(x):
    m = re.search(r"\(([\d.]+)\)", x)
    return float(m.group(1)) if m else None


def parse_detail(path):
    t = open(path, encoding="utf-8").read()
    g = lambda rx: float(re.search(rx, t).group(1))
    d = {"file": os.path.basename(path),
         "name": re.search(r"Team Name: (.*?)<br", t).group(1).strip(),
         "roster": [x.strip() for x in re.search(r"Roster: (.*?)<br", t).group(1).split(",")],
         "final": g(r"Final Rank Value \(([\d.]+)\)"), "seed": g(r"Starting Rank Value \(([\d.]+)\)"),
         "offered": g(r"Bounty Offered:\s*([\d.]+)"), "collected": g(r"Bounty Collected:\s*([\d.]+)"),
         "network": g(r"Opponent Network:\s*([\d.]+)"), "lan": g(r"LAN Wins:\s*([\d.]+)"),
         "win_sum": g(r"sum of their top 10 scaled winnings \(\$([\d,.]+)\)".replace("[\\d.]", "[\\d,.]")) if False else None}
    m = re.search(r"top 10 scaled winnings \(\$([\d,.]+)\)", t)
    d["win_sum"] = float(m.group(1).replace(",", "")) if m else 0.0
    m = re.search(r"5th highest value among all rosters \(\$([\d,.]+)\)", t)
    d["win_ref"] = float(m.group(1).replace(",", "")) if m else None
    mm = re.search(r"400 \+ \( \( [\d.]+ - ([\d.]+) \) / \( ([\d.]+) - [\d.]+ \) \)", t)
    d["min"], d["max"] = (float(mm.group(1)), float(mm.group(2))) if mm else (0.0, 1.0)
    rows = []
    for line in t.split('id="table1"', 1)[-1].split('id="table2"', 1)[0].splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) < 13 or not c[1].isdigit():
            continue
        v = lambda i: _num(c[i]) if c[i] != "-" else None
        rows.append({"n": int(c[1]), "id": int(c[2]), "date": c[3], "opp": c[4], "w": c[5] == "W",
                     "age": float(c[6]), "ew": v(7), "bc": v(8), "bca": _adj(c[8]), "net": v(9), "neta": _adj(c[9]),
                     "lan": v(10), "lana": _adj(c[10]), "h2h": float(c[11]),
                     "roster": [x.strip() for x in c[12].split(",")]})
    d["rows"] = sorted(rows, key=lambda r: r["n"])
    prizes = []
    for line in t.split('id="table2"', 1)[-1].splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) >= 6 and re.match(r"\d{4}-\d\d-\d\d$", c[1]):
            prizes.append({"date": c[1], "age": float(c[2]), "prize": _num(c[3]), "scaled": _num(c[4])})
    d["prizes"] = prizes
    return d


def load_snapshot(root, day):
    teams = []
    for line in open(f"{root}/standings_global_{day}.md", encoding="utf-8"):
        c = [x.strip() for x in line.split("|")]
        m = re.search(r"\((details/[^)]+)\)", line)
        if len(c) > 4 and c[1].isdigit() and m and os.path.exists(f"{root}/{m.group(1)}"):
            d = parse_detail(f"{root}/{m.group(1)}")
            d["rank"], d["points"] = int(c[1]), int(c[2])
            teams.append(d)
    return teams


def check_glicko(teams):
    """Сверка: пересчитать личные встречи по стартовым очкам и сравнить с итогом Valve."""
    # все матчи: id → стороны
    M = {}
    for i, d in enumerate(teams):
        for r in d["rows"]:
            M.setdefault(r["id"], []).append((i, r))
    # рейтинг соперников вне таблицы восстанавливаем из H2H их противника (решаем уравнение)
    rank = {i: d["seed"] for i, d in enumerate(teams)}
    ghost = {}
    errs = []
    for mid in sorted(M, reverse=True):  # меньше id — свежее
        sides = M[mid]
        if len(sides) == 2:
            (i, r), (j, s) = sides
            ri, rj = rank[i], rank[j]
            di = glicko(ri, rj, 1.0 if r["w"] else 0.0, r["age"])
            dj = glicko(rj, ri, 1.0 if s["w"] else 0.0, s["age"])
            errs.append(abs(di - r["h2h"]))
            errs.append(abs(dj - s["h2h"]))
            rank[i] += di; rank[j] += dj
        else:
            (i, r), = sides
            key = (r["opp"], tuple(sorted(r["roster"])))
            # найти ro, при котором glicko(rank[i], ro) == h2h
            lo, hi = -2000.0, 5000.0
            sc = 1.0 if r["w"] else 0.0
            if abs(r["h2h"]) < 1e-9 or r["age"] == 0:
                d = glicko(rank[i], 1000, sc, r["age"])
            else:
                for _ in range(80):
                    mid_ = (lo + hi) / 2
                    # победа: дельта растёт с ro; поражение: дельта (отрицательная) растёт по модулю с убыванием ro
                    if glicko(rank[i], mid_, sc, r["age"]) < r["h2h"]:
                        lo = mid_
                    else:
                        hi = mid_
                ghost.setdefault(r["opp"], []).append(round((lo + hi) / 2, 1))
                d = r["h2h"]
            rank[i] += d
    fin = [abs(rank[i] - d["final"]) for i, d in enumerate(teams)]
    return errs, fin, ghost


DAY0 = date(1970, 1, 1).toordinal()


def day_of(s):
    return date.fromisoformat(s).toordinal() - DAY0


def age(t, now):
    """Вес давности Valve: 1 за последние 30 дней, линейно до 0 к 180 дням (ranking_context.js)."""
    return min(1.0, max(0.0, (t - (now - 180)) / 150))


def snapshot_end(teams):
    """Момент снимка (в днях): по весам давности матчей, у которых он между 0 и 1."""
    lo, hi = -1e18, 1e18
    for d in teams:
        for r in d["rows"]:
            if 0.002 < r["age"] < 0.998:
                D = day_of(r["date"])
                lo = max(lo, D + 180 - (r["age"] + 0.0005) * 150)
                hi = min(hi, D + 1 + 180 - (r["age"] - 0.0005) * 150)
    return (lo + hi) / 2


def _time(day, a, end):
    D = day_of(day)
    if 0.0 < a < 1.0:
        return min(D + 0.999, max(D, end - 180 + a * 150))
    return D + 0.5


def build_dataset(teams):
    """Набор данных из разборов снимка: команды, призовые, матчи (победитель, LAN, вес турнира)."""
    end = snapshot_end(teams)
    T = {}
    for i, d in enumerate(teams):
        T["T%d" % i] = {"name": d["name"], "roster": d["roster"], "listed": True, "rank": d["rank"],
                         "points": d["points"], "seed": d["seed"],
                         "prizes": [(_time(p["date"], p["age"], end), p["prize"]) for p in d["prizes"]]}
    sides = {}
    for i, d in enumerate(teams):
        for r in d["rows"]:
            sides.setdefault(r["id"], []).append(("T%d" % i, r))
    M = []
    ghost_obs = {}
    for mid, ss in sides.items():
        if len(ss) == 2:
            (a, ra), (b, rb) = ss
            if not ra["w"]:
                (a, ra), (b, rb) = (b, rb), (a, ra)
            M.append({"id": mid, "t": _time(ra["date"], ra["age"], end), "w": a, "l": b, "wr": ra})
        else:
            (a, r), = ss
            g = "~" + r["opp"]
            T.setdefault(g, {"name": r["opp"], "listed": False, "prizes": [], "rat": {}})
            m = {"id": mid, "t": _time(r["date"], r["age"], end), "w": a if r["w"] else g, "l": g if r["w"] else a,
                 "wr": r if r["w"] else None, "own": r}
            M.append(m)
            if r["w"]:
                o = ghost_obs.setdefault(g, {})
                for f in ("bc", "net"):
                    if r[f] is not None and r["date"] >= o.get(f + "_d", ""):
                        o[f], o[f + "_d"] = r[f], r["date"]
    M.sort(key=lambda m: (m["t"], -m["id"]))
    # рейтинг «призраков» (команд вне таблицы) в каждом матче — из личной встречи их соперника
    for m in M:
        if "own" not in m:
            continue
        r = m["own"]
        g = m["l"] if r["w"] else m["w"]
        m["ghost"] = g
        m["h2h_obs"] = r["h2h"]
    for g, o in ghost_obs.items():
        T[g]["off"], T[g]["net"] = o.get("bc", 0.0), o.get("net", 0.0)
    # LAN и вес турнира у побед
    by_team_day = {}
    for m in M:
        r = m.get("wr")
        if r and r["ew"] is not None:
            by_team_day.setdefault(m["w"], []).append((m["t"], r["ew"]))
    for i, d in enumerate(teams):  # 10-е видимое значение LAN: невидимая победа могла быть LAN, только если не больше его
        vis = sorted((r["lana"] for r in d["rows"] if r["lana"] is not None), reverse=True)
        T["T%d" % i]["lan_floor"] = vis[9] if len(vis) >= 10 else -1.0
    lan_days = {}
    for m in M:
        r = m.get("wr")
        if r and r["lan"] is not None:
            lan_days.setdefault(m["w"], set()).add(int(m["t"]))
    for m in M:
        r = m.get("wr")
        m["ew_known"] = bool(r and r["ew"] is not None)
        if r and r["lan"] is not None:
            m["lan"] = r["lan"] >= 1
        elif r and age(m["t"], end) > T[m["w"]].get("lan_floor", 1.0) + 0.0005:
            m["lan"] = False
        else:  # не видно: LAN, если в те же дни у команды есть видимая LAN-победа
            m["lan"] = any(abs(int(m["t"]) - x) <= 2 for x in lan_days.get(m["w"], ()))
        if r and r["ew"] is not None:
            m["ew"] = r["ew"]
        else:
            near = [e for t, e in by_team_day.get(m["w"], []) if abs(t - m["t"]) <= 3]
            m["ew"] = max(near) if near else 0.3
    return {"teams": T, "matches": M, "end": end}


def nth(vals, n=5):
    v = sorted(vals, reverse=True)
    return v[min(n, len(v)) - 1] if v else 0.0


def top10(vals):
    return sum(sorted(vals, reverse=True)[:10])


def compute(ds, now=None, matches=None, prizes=None):
    """Полный пересчёт VRS на момент now (дни): сид по 4 факторам → Глико по всем матчам по времени.
    matches — список матчей (по умолчанию из набора), prizes — {команда: [(t, $)]} дополнительно."""
    T = ds["teams"]
    M = ds["matches"] if matches is None else matches
    now = ds["end"] if now is None else now
    L = [k for k, d in T.items() if d["listed"]]
    A = lambda t: age(t, now)
    sw, dtd, lanw, wins = {}, {}, {}, {}
    for k in L:
        pz = T[k]["prizes"] + list((prizes or {}).get(k, []))
        sw[k] = top10([p * A(t) for t, p in pz])
        wins[k] = []
    last = {}
    for m in M:
        if m["w"] in wins:
            wins[m["w"]].append(m)
            key = (m["w"], m["l"])
            last[key] = max(last.get(key, -1e9), m["t"])
    for k in L:
        dtd[k] = 0.0
    for (w, l), t in last.items():
        dtd[w] += A(t)
    for k in L:
        lanw[k] = top10([A(m["t"]) for m in wins[k] if m.get("lan")]) / 10
    rw, rn, rl = nth(sw.values()), nth(dtd.values()), nth(lanw.values())
    off, own, lanp = {}, {}, {}
    for k in L:
        off[k] = min(sw[k] / rw, 1.0) if rw else 0.0
        own[k] = min(dtd[k] / rn, 1.0) if rn else 0.0
        lanp[k] = min(lanw[k] / rl, 1.0) if rl else 0.0
    for k, d in T.items():
        if not d["listed"]:
            off[k], own[k] = d.get("off", 0.0), d.get("net", 0.0)
    avg, info = {}, {}
    for k in L:
        bc = top10([off.get(m["l"], 0.0) * A(m["t"]) * m["ew"] for m in wins[k]]) / 10
        net = top10([own.get(m["l"], 0.0) * A(m["t"]) * m["ew"] for m in wins[k]]) / 10
        avg[k] = (curve(off[k]) + curve(bc) + net + lanp[k]) / 4
        info[k] = {"offered": curve(off[k]), "collected": curve(bc), "network": net, "lan": lanp[k]}
    mx = max(avg.values())
    rating = {k: 400 + 1600 * avg[k] / mx for k in L}
    seed = dict(rating)
    for m in sorted(M, key=lambda m: (m["t"], -m["id"])):
        w, l, a = m["w"], m["l"], A(m["t"])
        rw_ = rating[w] if w in rating else m.get("ghost_r", 1000.0)
        rl_ = rating[l] if l in rating else m.get("ghost_r", 1000.0)
        dw, dl = glicko(rw_, rl_, 1.0, a), glicko(rl_, rw_, 0.0, a)
        if w in rating:
            rating[w] += dw
        if l in rating:
            rating[l] += dl
    return {"rating": rating, "seed": seed, "factors": info, "refs": (rw, rn, rl), "max": mx, "off": off, "own": own}


def fit_hidden(ds, teams):
    """Невидимые победы (вне десятки) не должны попадать в десятку: ограничиваем их вес турнира сверху."""
    R = compute(ds)
    fl = {}
    for i, d in enumerate(teams):
        for f, k in (("bc", "bca"), ("net", "neta")):
            vis = sorted((r[k] for r in d["rows"] if r[k] is not None), reverse=True)
            fl[("T%d" % i, f)] = vis[9] if len(vis) >= 10 else None
    for m in ds["matches"]:
        if m["ew_known"] or not ds["teams"][m["w"]]["listed"]:
            continue
        a = age(m["t"], ds["end"])
        for f, v in (("bc", R["off"].get(m["l"], 0.0)), ("net", R["own"].get(m["l"], 0.0))):
            cap = fl.get((m["w"], f))
            if cap is not None and v * a > 0:
                m["ew"] = min(m["ew"], (cap + 0.0005) / (v * a))


def ghost_ratings(ds):
    """Рейтинг соперников вне таблицы в каждом матче — подбором по личной встрече из снимка."""
    T = ds["teams"]
    rating = {k: d["seed"] for k, d in T.items() if d["listed"]}
    for m in ds["matches"]:
        a = age(m["t"], ds["end"])
        if "ghost" not in m:
            w, l = m["w"], m["l"]
            dw, dl = glicko(rating[w], rating[l], 1.0, a), glicko(rating[l], rating[w], 0.0, a)
            rating[w] += dw; rating[l] += dl
            continue
        me = m["l"] if m["ghost"] == m["w"] else m["w"]
        sc = 1.0 if me == m["w"] else 0.0
        h = m["h2h_obs"]
        lo, hi = -3000.0, 6000.0
        if a > 0:
            for _ in range(70):
                mid = (lo + hi) / 2
                if glicko(rating[me], mid, sc, a) < h:
                    lo = mid
                else:
                    hi = mid
            m["ghost_r"] = (lo + hi) / 2
        else:
            m["ghost_r"] = 1000.0
        rating[me] += h


VERSION = 1


def prepare(root, day):
    """Снимок Valve → набор данных для compute (только то, что нужно; сохраняется в JSON)."""
    S = load_snapshot(root, day)
    ds = build_dataset(S)
    ghost_ratings(ds)
    fit_hidden(ds, S)
    keep = ("id", "t", "w", "l", "lan", "ew", "ghost_r")
    ds["matches"] = [{k: m[k] for k in keep if k in m} for m in ds["matches"]]
    for d in ds["teams"].values():
        for k in ("rat", "lan_floor"):
            d.pop(k, None)
    ds["day"], ds["v"] = day, VERSION
    return ds


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _strip(n):
    return re.sub(r"^(team|the)|(team|esports|esport|gaming|clan|club|cs|gg)$", "", n)


def place_rank(place):
    """«5-6th» → (5, 6), «1st» → (1, 1)."""
    m = re.match(r"\s*(\d+)(?:\s*-\s*(\d+))?", str(place or ""))
    if not m:
        return None
    a = int(m.group(1))
    return a, int(m.group(2) or a)


def _places(places):
    out = []
    for p in places or []:
        r = place_rank(p.get("place"))
        if r:
            out.append({"r": r, "usd": float(p.get("prize") or 0) + float(p.get("club") or 0),
                        "teams": list(p.get("teams") or ([p["team"]] if p.get("team") else []))})
    return sorted(out, key=lambda x: x["r"])


def prize_for_ranks(places, lo, hi):
    """Наименьшие призовые среди мест с lo по hi (гарантия: «если проиграет — займёт одно из этих мест»)."""
    v = [p["usd"] for p in _places(places) if p["r"][1] >= lo and p["r"][0] <= hi]
    return min(v) if v else 0.0


def worst_open(places):
    """Призовые худшего ещё не занятого места — минимум для живой команды, сыгравшей хотя бы матч (как у HLTV)."""
    for p in reversed(_places(places)):
        if len(p["teams"]) < p["r"][1] - p["r"][0] + 1:
            return p["usd"]
    return 0.0


def alive_count(places):
    P = _places(places)
    total = max((p["r"][1] for p in P), default=0)
    return total - sum(len(p["teams"]) for p in P)


def _losers(a):
    """Сколько команд вылетает в текущем раунде плей-офф из a живых (6 → 2 в 1/4, 4 → 2, 2 → 1)."""
    return a - 2 ** int(math.floor(math.log2(a - 1))) if a >= 2 else 0


def playoff_win_prize(places):
    """Гарантия победителя матча плей-офф: место проигравшего в следующем раунде (1/4 IEM Beijing → 4-е)."""
    a = alive_count(places)
    if a < 2:
        return 0.0
    a2 = a - _losers(a)
    if a2 <= 1:
        return prize_for_ranks(places, 1, 1)
    l2 = _losers(a2)
    return prize_for_ranks(places, a2 - l2 + 1, a2)


class Live:
    """Набор данных снимка + матчи после него + призовые идущих турниров — «живой» рейтинг, как на HLTV."""

    def __init__(self, ds):
        self.ds = ds
        T = ds["teams"]
        self.idx, self.idx2 = {}, {}
        for k, d in sorted(T.items(), key=lambda kv: kv[1].get("rank") or 9999):
            if d.get("listed"):
                self.idx.setdefault(norm(d["name"]), k)
                self.idx2.setdefault(_strip(norm(d["name"])), k)
        self.pairs = {}
        self.ghost_last = {}
        for m in ds["matches"]:
            self.pairs.setdefault(frozenset((m["w"], m["l"])), []).append(m["t"])
            for g in (m["w"], m["l"]):
                if g.startswith("~") and m.get("ghost_r") is not None:
                    self.ghost_last[g] = m["ghost_r"]
        self.new = []
        self.prizes = {}
        self._seen = set()

    def key(self, name):
        n = norm(name)
        return self.idx.get(n) or self.idx2.get(_strip(n)) or "~" + (name or "?")

    def name(self, k):
        return (self.ds["teams"].get(k) or {}).get("name") or k.lstrip("~")

    def _match(self, t, kw, kl, ew, lan, mid=None):
        m = {"id": mid if mid is not None else -1 - len(self.new) - len(self._seen), "t": t, "w": kw, "l": kl,
             "ew": ew, "lan": lan}
        for g in (kw, kl):
            if g.startswith("~") or g not in self.ds["teams"]:
                m["ghost_r"] = self.ghost_last.get(g, 1000.0)
        return m

    def add_match(self, t, a, b, a_won, ew=0.5, lan=False):
        """Сыгранный матч после снимка (t — дни от 1970). Уже учтённые в снимке (та же пара ±1 день) пропускаются."""
        ka, kb = self.key(a), self.key(b)
        if ka == kb or t <= self.ds["end"] - 30:
            return False
        pid = (frozenset((ka, kb)), round(t, 2))
        if pid in self._seen or any(abs(x - t) < 1.0 for x in self.pairs.get(frozenset((ka, kb)), ())):
            return False
        self._seen.add(pid)
        self.new.append(self._match(t, ka if a_won else kb, kb if a_won else ka, ew, lan))
        return True

    def add_prize(self, name, usd, t):
        k = self.key(name)
        if usd > 0 and not k.startswith("~"):
            self.prizes.setdefault(k, []).append((t, usd))

    def compute(self, now, extra_matches=(), extra_prizes=None):
        P = {k: list(v) for k, v in self.prizes.items()}
        for k, v in (extra_prizes or {}).items():
            P.setdefault(k, []).extend(v)
        return compute(self.ds, now=now, matches=self.ds["matches"] + self.new + list(extra_matches), prizes=P)

    def points(self, now):
        R = self.compute(now)["rating"]
        return {self.name(k): v for k, v in R.items()}

    def forecast(self, a, b, now, ew=1.0, lan=True, win_prize=(0.0, 0.0), both_prize=(0.0, 0.0), base=None):
        """Изменение очков A и B при победе каждой: {'a_win': (dA, dB), 'b_win': (dA, dB)}.
        win_prize — прибавка гарантированных призовых победителю (A, B), both_prize — обеим командам после матча."""
        ka, kb = self.key(a), self.key(b)
        if ka.startswith("~") or kb.startswith("~"):
            return None
        base = base or self.compute(now)["rating"]
        out = {}
        for lab, (w, l, i) in (("a_win", (ka, kb, 0)), ("b_win", (kb, ka, 1))):
            ex = {}
            for k, v in ((ka, both_prize[0]), (kb, both_prize[1]), (w, win_prize[i])):
                if v:
                    ex.setdefault(k, []).append((now, v))
            R = self.compute(now, [self._match(now, w, l, ew, lan, mid=-10 ** 9)], ex)["rating"]
            out[lab] = (R[ka] - base[ka], R[kb] - base[kb])
            out[lab + "_all"] = {k: R[k] - base[k] for k in R}  # сдвиг очков всех команд — для мест, как у HLTV
        return out


def _check_live(ds, data):
    """Сверка с живым рейтингом HLTV и образцами прогноза (data — выгрузка ветки data)."""
    import json
    from datetime import datetime
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() / 86400
    L = Live(ds)
    tours = json.load(open(os.path.join(data, "matches.json")))["tournaments"]
    tours = tours if isinstance(tours, list) else list(tours.values())
    played = {}
    for f in glob.glob(os.path.join(data, "results", "*.json")):
        r = json.load(open(f))
        for m in r["matches"]:
            if m.get("winner") is not None and len(m["teams"]) == 2:
                if L.add_match(ts(m["date"]), m["teams"][0]["name"], m["teams"][1]["name"], m["winner"] == 0, 1.0, True):
                    for tm in m["teams"]:
                        played.setdefault(r["tournament"], set()).add(tm["name"])
    for f in glob.glob(os.path.join(data, "team", "*.json")):
        t = json.load(open(f))
        for x in t.get("form", []):
            if x.get("won") is not None:
                s_ = (x.get("tier") or "").lower() == "s"
                L.add_match(ts(x["date"]), t["name"], x["opponent_name"], x["won"], 1.0 if s_ else 0.5, s_)
    rk = json.load(open(os.path.join(data, "ranking.json")))["vrs"]
    now = ts(rk.get("updated") or json.load(open(os.path.join(data, "ranking.json")))["updated"])
    for tr in tours:
        pl = (tr.get("hltv") or {}).get("places")
        if not pl or tr["name"] not in played:
            continue
        done = {n for p in pl for n in (p.get("teams") or [])}
        for p in _places(pl):
            for n in p["teams"]:
                L.add_prize(n, p["usd"], now)
        for n in played[tr["name"]] - done:
            L.add_prize(n, worst_open(pl), now)
    R = L.points(now)
    errs = [(r["rank"], r["name"], r["points"], round(R[L.name(L.key(r["name"]))] - r["points"], 1))
            for r in rk["teams"][:40] if not L.key(r["name"]).startswith("~")]
    print("живой рейтинг HLTV, топ-40: ср. ошибка %.2f" % (sum(abs(e[3]) for e in errs) / len(errs)))
    smp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "hltv_vrs_samples.json")
    if os.path.exists(smp):
        base = L.compute(now)["rating"]
        tot = 0
        for s_ in json.load(open(smp))["samples"]:
            a, b = [x.strip() for x in s_["match"].split(" vs ")]
            tr = next((t for t in tours if t["name"].split(" 20")[0] in s_["event"]), None)
            pl = (tr or {}).get("hltv", {}).get("places")
            if "QF" in s_["event"] or "SF" in s_["event"] or "Final" in s_["event"]:
                wp = playoff_win_prize(pl) - worst_open(pl)
                f = L.forecast(a, b, now, win_prize=(wp, wp), base=base)
            else:
                g = worst_open(pl)
                f = L.forecast(a, b, now, both_prize=(g, g), base=base)
            r = {"aw": f["a_win"][0], "bl": f["a_win"][1], "al": f["b_win"][0], "bw": f["b_win"][1]}
            e = sum(abs(round(r[k]) - s_[k]) for k in r)
            tot += e
            print("%-28s HLTV %+d/%+d %+d/%+d | наш %+d/%+d %+d/%+d" % (s_["match"], s_["aw"], s_["bl"], s_["al"], s_["bw"],
                  round(r["aw"]), round(r["bl"]), round(r["al"]), round(r["bw"])))
        print("сумма ошибок прогноза:", tot)


if __name__ == "__main__":
    import statistics as st
    root, day = sys.argv[1], sys.argv[2]
    ds = prepare(root, day)
    print(len([1 for d in ds["teams"].values() if d["listed"]]), "команд, матчей", len(ds["matches"]), "снимок", ds["end"])
    R = compute(ds)
    T = ds["teams"]
    e = sorted(((abs(R["rating"][k] - T[k]["points"]), T[k]["rank"]) for k in R["rating"]), key=lambda x: x[1])
    for lo, hi in ((1, 10), (11, 30), (31, 100), (101, 400)):
        x = [r[0] for r in e if lo <= r[1] <= hi]
        print("снимок, места %d-%d: ср. ошибка %.2f, макс %.2f" % (lo, hi, st.mean(x), max(x)))
    if len(sys.argv) > 3:
        _check_live(ds, sys.argv[3])
