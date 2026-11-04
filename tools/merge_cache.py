"""Слить второй кэш сборщика в основной, ничего не теряя.

Сбор новостей идёт долго (около часа) и держит свой кэш отдельно, а полный сбор и статистика — свой.
Перед каждым сбором оба объединяются: словари — по ключам (есть только в одном — берём оттуда),
числа (счётчики расхода нейросети за день) — большее, остальное — из основного кэша.

    python tools/merge_cache.py <основной каталог> <второй каталог>
"""
import json
import shutil
import sys
from pathlib import Path


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return max(a, b)
    return a


def main(base: Path, other: Path) -> None:
    if not other.is_dir():
        return
    base.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in other.rglob("*"):
        if not f.is_file():
            continue
        dst = base / f.relative_to(other)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            n += 1
            continue
        if f.suffix != ".json":
            continue
        try:
            a = json.loads(dst.read_text("utf-8"))
            b = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        m = merge(a, b)
        if m != a:
            dst.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), "utf-8")
            n += 1
    print(f"Кэш: слито файлов {n} из {other}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
