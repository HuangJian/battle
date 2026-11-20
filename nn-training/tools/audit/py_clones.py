"""近似克隆检测（`python.refactor.md` 附录 A 第 3 步）：归一化后的 12 行窗口跨模块重复。

与 `py_audit.py` 同规：住仓库里、路径从 `__file__` 推，读数可原样复跑。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # nn-training/（不依赖 cwd：从任何目录跑都一样）
DIRS = ["common", "hub", "remote", "trainer", "worker", "biz", "tools", "train"]
WIN = 12  # min duplicate block (normalized lines)


def norm(line: str) -> str:
    s = line.strip()
    if not s or s.startswith("#"):
        return ""
    s = re.sub(r"\s+", " ", s)
    return s


files = []
for d in DIRS:
    for p in (ROOT / d).rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        files.append(p)

import ast


def doc_rows(src: str) -> set[int]:
    rows: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return rows
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for r in range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1):
                    rows.add(r)
    return rows


windows: dict[str, list[tuple[str, int]]] = defaultdict(list)
for p in files:
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    lines = src.splitlines()
    dead = doc_rows(src)
    nl = ["" if (i + 1) in dead else norm(x) for i, x in enumerate(lines)]
    for i in range(len(nl) - WIN):
        chunk = [x for x in nl[i : i + WIN] if x]
        if len(chunk) < WIN:  # skip windows containing blanks
            continue
            continue
        h = hashlib.sha1("|".join(chunk).encode()).hexdigest()
        windows[h].append((str(p), i + 1))

dups = {h: v for h, v in windows.items() if len({f for f, _ in v}) > 1}
print(f"files={len(files)} dup_windows={len(dups)}")

# group by file pair
pairs: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
for _h, v in dups.items():
    locs = sorted({(f, ln) for f, ln in v})
    for a in range(len(locs)):
        for b in range(a + 1, len(locs)):
            (f1, l1), (f2, l2) = locs[a], locs[b]
            if f1 != f2:
                pairs[(f1, f2)].append((l1, l2))

ranked = sorted(pairs.items(), key=lambda kv: -len(kv[1]))
for (f1, f2), hits in ranked[:25]:
    print(f"{len(hits):4d} windows (~{len(hits)*WIN} lines)  {f1}  <->  {f2}")

# self-duplication density
per_file: dict[str, int] = defaultdict(int)
for _h, v in dups.items():
    for f in {x for x, _ in v}:
        per_file[f] += 1
print("\n== files with most duplicated windows ==")
for f, n in sorted(per_file.items(), key=lambda kv: -kv[1])[:15]:
    print(f"{n:4d} (~{n*WIN} lines)  {f}")
