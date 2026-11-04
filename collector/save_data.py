"""Сохранить данные в ветку data, не затирая чужие свежие изменения.

Сборы идут параллельно (новости, статистика, полный), поэтому перед записью берём последнюю версию ветки
и сливаем: своё накладываем поверх, а ленту и новости матчей оставляем самые свежие из двух версий.
Запись — с проверкой, что ветку никто не обновил за это время; если обновил — повторяем.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MODE = sys.argv[1]            # news | stats | full
OURS = Path("docs/data")
REMOTE = sys.argv[2]          # адрес репозитория с токеном


def run(*a, cwd=None, check=True):
    return subprocess.run(a, cwd=cwd, check=check, capture_output=True, text=True)


def load(p: Path):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def newer(a, b, key="updated"):
    return ((a or {}).get(key) or "") >= ((b or {}).get(key) or "")


def merge(dst: Path) -> None:
    if MODE in ("stats", "full"):  # своё — поверх; набор карточек матчей и итогов — как у нас
        for sub in ("match", "results", "team"):
            if (OURS / sub).exists() and (dst / sub).exists():
                for f in (dst / sub).glob("*.json"):
                    if not (OURS / sub / f.name).exists():
                        f.unlink()
        for f in OURS.rglob("*"):
            if f.is_file() and f.name != "feed.json":
                rel = f.relative_to(OURS)
                if rel.parts[0] == "match" and (dst / rel).exists():
                    a, b = load(f), load(dst / rel)
                    if a is not None and b is not None and not newer(a.get("news"), b.get("news")):
                        a["news"] = b["news"]  # в ветке новости свежее (их обновляет быстрый сбор)
                        (dst / rel).write_text(json.dumps(a, ensure_ascii=False, separators=(",", ":")), "utf-8")
                        continue
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst / rel)
    # лента — самая свежая
    a, b = load(OURS / "feed.json"), load(dst / "feed.json")
    if a is not None and (b is None or newer(a, b)):
        shutil.copy2(OURS / "feed.json", dst / "feed.json")
    if MODE == "news":  # только новости матчей
        for f in (OURS / "match").glob("*.json"):
            g = dst / "match" / f.name
            a, b = load(f), load(g)
            if a is not None and b is not None and newer(a.get("news"), b.get("news")):
                b["news"] = a["news"]
                g.write_text(json.dumps(b, ensure_ascii=False, separators=(",", ":")), "utf-8")


for attempt in range(6):
    tmp = Path(tempfile.mkdtemp())
    r = run("git", "fetch", "-q", "--depth", "1", REMOTE, "data", check=False)
    sha = run("git", "rev-parse", "FETCH_HEAD", check=False).stdout.strip() if r.returncode == 0 else ""
    if sha:
        arch = subprocess.run(["git", "archive", sha], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=arch, check=True)
    merge(tmp)
    run("git", "init", "-q", "-b", "data", cwd=tmp)
    run("git", "-c", "user.name=cs-prep-bot", "-c", "user.email=cs-prep-bot@users.noreply.github.com", "add", "-A", cwd=tmp)
    run("git", "-c", "user.name=cs-prep-bot", "-c", "user.email=cs-prep-bot@users.noreply.github.com",
        "commit", "-qm", f"Данные ({MODE}): {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC", cwd=tmp)
    lease = f"--force-with-lease=data:{sha}" if sha else "--force"
    p = run("git", "push", "-q", lease, REMOTE, "HEAD:data", cwd=tmp, check=False)
    if p.returncode == 0:
        shutil.rmtree(tmp / ".git")
        shutil.rmtree(OURS)
        shutil.copytree(tmp, OURS)  # сайт публикуем из слитой версии
        print(f"Данные сохранены ({MODE}), попытка {attempt + 1}")
        sys.exit(0)
    print("Ветку данных обновили параллельно — сливаю заново:", p.stderr.strip()[:200])
    time.sleep(4 + attempt * 3)
sys.exit(1)
