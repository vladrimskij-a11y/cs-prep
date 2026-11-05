"""Проверка прогноза вето на сыгранных сериях с настоящим вето (dust2).
Запуск: взять ветку data в папку (git archive origin/data | tar -x -C /tmp/dd) и
python tools/veto_backtest.py /tmp/dd — печатает, как часто угадываются пики, десайдер, первый бан и сыгранные карты,
для прежнего способа и нового (с подбором весов на первой половине серий, проверкой на второй)."""
import json, glob, re, math, sys, itertools
from datetime import datetime, timedelta
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
def pd(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
ms = []
for f in glob.glob(sys.argv[1] + "/results/*.json"):
    for m in json.load(open(f)).get("matches") or []:
        if m.get("veto") and len(m.get("teams") or []) == 2:
            ms.append(m)
ms = list({m["id"]: m for m in ms}.values())
ms.sort(key=lambda m: m["date"])
def side(m, name):
    k = norm(name)
    for i, t in enumerate(m["teams"]):
        for x in (t["name"], t.get("acronym")):
            a = norm(x)
            if a and (a == k or (min(len(a), len(k)) >= 3 and (a in k or k in a))):
                return i
    return None
# per series normalize: team keys (bo3 names), steps with side index
S = []
for m in ms:
    st = []
    ok = True
    for s in m["veto"]:
        i = side(m, s["team"]) if s["team"] else None
        if s["act"] != "decider" and i is None: ok = False
        st.append((i, s["act"], s["map"]))
    if ok:
        S.append({"d": pd(m["date"]), "bo": m["bo"], "t": [norm(t["name"]) for t in m["teams"]], "w": m.get("winner"), "st": st,
                  "maps": [(x["map"], x.get("winner")) for x in m.get("maps") or []]})
print("series", len(S), file=sys.stderr)
POOL_DAYS = 90
def hist(team, before, days=POOL_DAYS):
    out = []
    for s in S:
        if s["d"] >= before: break
        if s["d"] < before - timedelta(days=days): continue
        if team in s["t"]: out.append((s, s["t"].index(team)))
    return out

def profile(team, before, half=None):
    """half=None — без затухания (как сейчас); иначе вес 0.5^(дни/half)."""
    H = hist(team, before)
    P, B, F, D, W, PL, n = {}, {}, {}, {}, {}, {}, 0.0
    vs = {}
    for s, i in H:
        w = 1.0 if not half else 0.5 ** ((before - s["d"]).days / half)
        n += w
        mine = [x for x in s["st"] if x[0] == i]
        for k, (j, act, mp) in enumerate(mine):
            tgt = P if act == "pick" else B
            tgt[mp] = tgt.get(mp, 0) + w
            if act == "ban" and k == 0: F[mp] = F.get(mp, 0) + w
            if act == "pick":
                o = s["t"][1 - i]; vs.setdefault(o, {}); vs[o][mp] = vs[o].get(mp, 0) + 1
        for mp, win in s["maps"]:
            if win is None: continue
            PL[mp] = PL.get(mp, 0) + w; W[mp] = W.get(mp, 0) + (w if win == i else 0)
    return {"n": n, "P": P, "B": B, "F": F, "W": W, "PL": PL, "vs": vs}

def strength(pr, mp, k=3.0):
    # победы на карте со «сжатием» к 50%: при малом числе игр не верим крайним значениям
    return (pr["W"].get(mp, 0) + 0.5 * k) / (pr["PL"].get(mp, 0) + k)

ORDER = {3: ["ban", "ban", "pick", "pick", "ban", "ban"], 1: ["ban"] * 6, 5: ["ban", "ban", "pick", "pick", "pick", "pick"]}

def predict_old(s, pool):
    pr = [profile(t, s["d"]) for t in s["t"]]
    first = s["st"][0][0]
    left, out = list(pool), []
    for k, act in enumerate(ORDER[s["bo"]]):
        i = first if k % 2 == 0 else 1 - first
        cnt = dict(pr[i]["B"] if act == "ban" else pr[i]["P"])
        if act == "ban":
            for mp, c in pr[i]["F"].items(): cnt[mp] = cnt.get(mp, 0) + c
        own = (lambda mp: strength(pr[i], mp, 2)) if act == "pick" else (lambda mp: -strength(pr[i], mp, 2))
        mp = max(left, key=lambda mp: (cnt.get(mp, 0), own(mp)))
        left.remove(mp); out.append((i, act, mp))
    out.append((None, "decider", left[0] if left else None))
    return out

def predict_new(s, pool, prm):
    half, a, b, c, d, e = prm
    pr = [profile(t, s["d"], half) for t in s["t"]]
    first = s["st"][0][0]
    left, out, myb = list(pool), [], [0, 0]
    for k, act in enumerate(ORDER[s["bo"]]):
        i = first if k % 2 == 0 else 1 - first
        me, op = pr[i], pr[1 - i]
        nm, no = max(me["n"], 1e-9), max(op["n"], 1e-9)
        def sc(mp):
            dx = strength(me, mp) - strength(op, mp)
            if act == "ban":
                v = a * me["B"].get(mp, 0) / nm - c * dx + d * op["P"].get(mp, 0) / no
                if myb[i] == 0: v += b * me["F"].get(mp, 0) / nm
                return v
            v = a * me["P"].get(mp, 0) / nm + c * dx - d * op["B"].get(mp, 0) / no
            v += e * me["vs"].get(s["t"][1 - i], {}).get(mp, 0)  # что уже пикали именно против этой команды
            return v
        mp = max(left, key=sc)
        if act == "ban": myb[i] += 1
        left.remove(mp); out.append((i, act, mp))
    out.append((None, "decider", left[0] if left else None))
    return out

def pool_at(d):
    seen = {}
    for s in S:
        if s["d"] >= d: break
        if s["d"] < d - timedelta(days=45): continue
        for x in s["st"]: seen[x[2]] = seen.get(x[2], 0) + 1
    return [mp for mp, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:7]

def score(pred, s):
    act = s["st"]
    played_a = {x[2] for x in act if x[1] in ("pick", "decider")}
    played_p = {x[2] for x in pred if x[1] in ("pick", "decider")}
    picks_a = [x[2] for x in act if x[1] == "pick"]; picks_p = [x[2] for x in pred if x[1] == "pick"]
    return {"played": len(played_a & played_p) / max(1, len(played_a)),
            "pick_exact": sum(1 for x, y in zip(picks_a, picks_p) if x == y) / max(1, len(picks_a)),
            "decider": 1.0 if (act[-1][1] == "decider" and pred[-1][2] == act[-1][2]) else 0.0,
            "first_ban": 1.0 if act[0][2] == pred[0][2] else 0.0}

def evaluate(fn, subset):
    acc = {}; n = 0
    for s in subset:
        if s["bo"] != 3 or len(s["st"]) != 7: continue
        if min(len(hist(t, s["d"])) for t in s["t"]) < 3: continue
        pool = pool_at(s["d"])
        if len(pool) < 7 or any(x[2] not in pool for x in s["st"]): continue
        r = score(fn(s, pool), s); n += 1
        for k, v in r.items(): acc[k] = acc.get(k, 0) + v
    return n, {k: round(100 * v / max(1, n), 1) for k, v in acc.items()}

if __name__ == "__main__":
    cut = S[len(S) // 2]["d"]
    tr = [s for s in S if s["d"] < cut]; te = [s for s in S if s["d"] >= cut]
    print("old train", evaluate(predict_old, tr)); print("old test ", evaluate(predict_old, te))
    best = None
    for prm in itertools.product([None, 20, 45], [1.0], [0.0, 0.5, 1.0], [0.0, 1.0, 3.0], [0.0, 0.5, 1.0], [0.0, 0.3, 1.0]):
        n, r = evaluate(lambda s, p: predict_new(s, p, prm), tr)
        sc = r["played"] + r["pick_exact"] + r["decider"] + 0.5 * r["first_ban"]
        if not best or sc > best[0]: best = (sc, prm, r)
    print("best params", best[1], "train", best[2])
    print("new test ", evaluate(lambda s, p: predict_new(s, p, best[1]), te))
