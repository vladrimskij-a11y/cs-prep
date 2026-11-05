"""Проверка прогноза VRS («что даст победа») на месячных снимках Valve.

    git clone --depth 1 https://github.com/ValveSoftware/counter-strike_regional_standings valve
    python tools/vrs_backtest.py valve/live/2026

Для каждой победы между снимками k и k+1: прогноз по факторам снимка k (как на сайте) против настоящего
вклада этой победы в снимке k+1 (сид с ней и без неё). 11-е значение в топ-10 у Valve не видно, поэтому
настоящий вклад — вилка: «11-го нет» … «11-е равно 10-му». Команды у потолка сида (≥1900) не берём:
у лидера вклад уходит в общую шкалу. Результат 06.11: без пересчёта давности прогноз у топ-30 на нижнем краю
вилки, с пересчётом давности на день матча (valve_aged) ближе к середине: ошибка меньше на 7–24%
(топ-10: 19,5 → 14,8 очка, места 11–30: 15,8 → 12,7).
"""
import glob, math, os, re, statistics as st, sys
from datetime import date

ROOT = sys.argv[1] if len(sys.argv) > 1 else "valve/live/2026"
DAY = lambda s: date.fromisoformat(s.replace("_", "-")).toordinal()


def curve(x):
    return 1.0 if x >= 1 else 0.0 if x <= 0 else 1 / (1 + abs(math.log10(x)))


def inv_curve(y):
    return 1.0 if y >= 1 else 0.0 if y <= 0 else 10 ** -(1 / y - 1)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def num(x):
    m = re.match(r"\s*([\d.]+)", x)
    return float(m.group(1)) if m else None


def adj(x):
    m = re.search(r"\(([\d.]+)\)", x)
    return float(m.group(1)) if m else None


def parse(path):
    t = open(path, encoding="utf-8").read()
    g = lambda rx: float(re.search(rx, t).group(1))
    d = {"offered": g(r"Bounty Offered:\s*([\d.]+)"), "seed": g(r"Starting Rank Value \(([\d.]+)\)"),
         "name": re.search(r"Team Name: (.*?)<br", t).group(1).strip()}
    mm = re.search(r"400 \+ \( \( [\d.]+ - ([\d.]+) \) / \( ([\d.]+) - [\d.]+ \) \)", t)
    d["min"], d["max"] = (float(mm.group(1)), float(mm.group(2))) if mm else (0.0, 1.0)
    rows = []
    for line in t.split('id="table1"', 1)[-1].split('id="table2"', 1)[0].splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) < 13 or not c[1].isdigit():
            continue
        v = lambda i: num(c[i]) if c[i] != "-" else None
        rows.append({"n": int(c[1]), "date": c[3], "opp": c[4], "w": c[5] == "W", "ew": v(7),
                     "bc": v(8), "bca": adj(c[8]), "net": v(9), "neta": adj(c[9]), "lan": v(10), "lana": adj(c[10])})
    d["rows"] = sorted(rows, key=lambda r: r["n"])
    return d


def snap(day):
    out = []
    for line in open(f"{ROOT}/standings_global_{day}.md", encoding="utf-8"):
        c = [x.strip() for x in line.split("|")]
        m = re.search(r"\((details/[^)]+)\)", line)
        if len(c) > 4 and c[1].isdigit() and m and os.path.exists(f"{ROOT}/{m.group(1)}"):
            d = parse(f"{ROOT}/{m.group(1)}")
            d["rank"] = int(c[1])
            out.append(d)
    return out


def top(vals, v=None):
    return sum(sorted(vals + ([v] if v is not None else []), reverse=True)[:10]) / 10


def seed(W, extra=None):
    avg = (W["offered"] + curve(top(W["bc"], extra and extra[0])) + top(W["net"], extra and extra[1])
           + top(W["lan"], extra and extra[2])) / 4
    return min(2000.0, 400 + (avg - W["min"]) / max(1e-6, W["max"] - W["min"]) * 1600)


def lists(d, at=None):
    """Списки факторов: как в разборе (at=None) или с весом давности на день at (как valve_aged в сборщике)."""
    L = {"bc": [], "net": [], "lan": []}
    for r in d["rows"]:
        if at is None:
            for f, k in (("bc", "bca"), ("net", "neta"), ("lan", "lana")):
                if r[k] is not None:
                    L[f].append(r[k])
        elif r["w"]:
            a = min(1.0, max(0.0, (DAY(r["date"]) - at + 180) / 150))
            if r["bc"] is not None and r["ew"] is not None:
                L["bc"].append(r["bc"] * r["ew"] * a)
            if r["net"] is not None and r["ew"] is not None:
                L["net"].append(r["net"] * r["ew"] * a)
            if r["lan"] is not None:
                L["lan"].append(r["lan"] * a)
    return L


def main():
    days = sorted(os.path.basename(x) for x in glob.glob(f"{ROOT}/details/*"))
    S = {d: snap(d) for d in days}
    R = []
    for k in range(len(days) - 1):
        A, B = S[days[k]], S[days[k + 1]]
        cut = max(r["date"] for d in A for r in d["rows"])
        byn = {}
        for d in A:
            byn.setdefault(norm(d["name"]), d)
        bas = {}
        for d in A[:60]:
            for r in d["rows"]:
                if r["w"]:
                    o = bas.setdefault(norm(r["opp"]), {})
                    for f in ("bc", "net"):
                        if r[f] is not None and r["date"] >= o.get(f + "_d", ""):
                            o[f], o[f + "_d"] = r[f], r["date"]
        for dB in B[:150]:
            dA = byn.get(norm(dB["name"]))
            if not dA or dA["seed"] >= 1900 or dB["seed"] >= 1900:
                continue
            WB = dict(dB, **lists(dB))
            sB = seed(WB)
            prior = []
            for r in dB["rows"]:
                if r["date"] <= cut or not r["w"]:
                    continue
                hi, lo = (dict(WB, **{f: list(WB[f]) for f in ("bc", "net", "lan")}) for _ in range(2))
                for f, kk in (("bc", "bca"), ("net", "neta"), ("lan", "lana")):
                    if r[kk] is not None:
                        hi[f].remove(r[kk]); lo[f].remove(r[kk])
                        if len(WB[f]) >= 10:
                            lo[f].append(min(WB[f]))
                opA = byn.get(norm(r["opp"]))
                o = bas.get(norm(r["opp"])) or {}
                ob = o.get("bc", inv_curve(opA["offered"]) if opA else 0.0)
                on = o.get("net", 0.0)
                ew = r["ew"] if r["ew"] is not None else 0.5
                new = (ob * ew, on * ew, r["lan"] or 0.0)
                res = {"rank": dA["rank"], "hi": sB - seed(hi), "lo": sB - seed(lo)}
                for lab, L in (("now", lists(dA)), ("aged", lists(dA, DAY(r["date"])))):
                    W = dict(dA, **{f: L[f] + [x[i] for x in prior] for i, f in enumerate(("bc", "net", "lan"))})
                    res[lab] = max(0.0, seed(W, new) - seed(W))
                R.append(res)
                prior.append(new)
    print("места   побед  настоящий вклад   без пересчёта (ошибка)   с пересчётом давности (ошибка)")
    for lo, hi in ((1, 200), (1, 10), (11, 30), (31, 70), (71, 200)):
        rows = [r for r in R if lo <= r["rank"] <= hi]
        m = lambda key: st.mean(r[key] for r in rows)
        e = lambda key: st.mean(abs((r["hi"] + r["lo"]) / 2 - r[key]) for r in rows)
        print(f"{lo:3d}-{hi:<3d} {len(rows):6d}  {m('lo'):5.1f}…{m('hi'):5.1f}     {m('now'):5.1f} ({e('now'):4.1f})            {m('aged'):5.1f} ({e('aged'):4.1f})")


if __name__ == "__main__":
    main()
