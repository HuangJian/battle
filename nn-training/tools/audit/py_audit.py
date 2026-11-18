"""nn-training 的 AST 度量（`python.refactor.md` 附录 A 第 2 步的唯一实现）。

为什么它住在仓库里而不是 gitignored 的 `tmp/`：这份文档里每个读数（LOC / 函数形状 /
`except` 分布 / 模块级容器 / env 直读）都要能被下一个人**原样复跑**，否则「基线只许降」
就成了凭记忆的承诺。路径从 `__file__` 推，不依赖 cwd。
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # nn-training/（不依赖 cwd：从任何目录跑都一样）
SKIP = {".venv", "__pycache__", "tmp", "weights", "ipynb", "node_modules"}
PKG_DIRS = ["common", "hub", "remote", "trainer", "worker", "biz", "tools", "tests", "e2e"]
TARGETS = [ROOT / d for d in PKG_DIRS] + [ROOT / "conftest.py", ROOT / "train"]


def iter_py():
    for p in TARGETS:
        if p.is_file() and p.suffix == ".py":
            yield p
            continue
        if not p.exists():
            continue
        for f in p.rglob("*.py"):
            if SKIP & set(f.parts):
                continue
            yield f


def complexity(node: ast.AST) -> int:
    c = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                          ast.With, ast.AsyncWith, ast.Assert, ast.comprehension)):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif isinstance(n, ast.IfExp):
            c += 1
    return c


def max_nest(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        d = depth
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
                              ast.Try, ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = depth + 1
        best = max(best, max_nest(child, d))
    return best


# 审计脚本装的是**任意形状**的 AST 摘要（键随被扫代码而定），用 `Any` 是这里唯一诚实的注；
# `tools/` 不受 G14 约束（与 G3/G4/G12 同款豁免，见 `tests/test_any_budget.py`）。
funcs: list[dict[str, Any]] = []
classes: list[dict[str, Any]] = []
file_stats: list[dict[str, Any]] = []
mutable_globals: list[tuple[str, int, str]] = []
excs: Counter[str] = Counter()
any_ann = 0
ann_total = 0
untyped_defs = 0
total_defs = 0
print_calls = 0
env_reads = 0
sleep_calls = 0
subprocess_calls = 0
global_stmts = 0

for f in iter_py():
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError as e:
        file_stats.append({"file": str(f), "syntax_error": str(e)})
        continue
    lines = src.splitlines()
    comments = sum(1 for ln in lines if ln.strip().startswith("#"))
    docstrings = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(n)
            if ds:
                docstrings += ds.count("\n") + 1
    file_stats.append({
        "file": str(f),
        "loc": len(lines),
        "code": sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#")),
        "comments": comments,
        "docstrings": docstrings,
        "max_line": max((len(ln) for ln in lines), default=0),
        "bytes": len(src.encode("utf-8")),
    })
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total_defs += 1
            args = n.args.posonlyargs + n.args.args + n.args.kwonlyargs
            has_ann = bool(n.returns) or any(a.annotation for a in args)
            if not has_ann:
                untyped_defs += 1
            for a in args:
                if a.annotation is not None:
                    ann_total += 1
                    if isinstance(a.annotation, ast.Name) and a.annotation.id == "Any":
                        any_ann += 1
                if a.arg not in ("self", "cls"):
                    ann_total += 0
            funcs.append({
                "file": str(f),
                "name": n.name,
                "line": n.lineno,
                "length": (n.end_lineno or n.lineno) - n.lineno + 1,
                "params": len([a for a in args if a.arg not in ("self", "cls")]),
                "complexity": complexity(n),
                "nest": max_nest(n),
                "doc_lines": (ast.get_docstring(n) or "").count("\n") + (1 if ast.get_docstring(n) else 0),
            })
        elif isinstance(n, ast.ClassDef):
            meths = [m for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes.append({
                "file": str(f), "name": n.name, "line": n.lineno,
                "methods": len(meths),
                "length": (n.end_lineno or n.lineno) - n.lineno + 1,
                "bases": [ast.unparse(b) for b in n.bases],
            })
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            val = n.value
            mod_level = False
            for t in targets:
                if (isinstance(t, ast.Name) and t.id.isupper()) or (
                    isinstance(t, ast.Name) and t.id.startswith("_")
                ):
                    pass
            if isinstance(val, (ast.List, ast.Dict, ast.Set, ast.Call)):
                for t in targets:
                    if isinstance(t, ast.Name):
                        mutable_globals.append((str(f), n.lineno, ast.unparse(n)[:100]))
        elif isinstance(n, ast.ExceptHandler):
            excs[ast.unparse(n.type) if n.type else "BARE"] += 1
        elif isinstance(n, ast.Global):
            global_stmts += len(n.names)
        elif isinstance(n, ast.Call):
            fn = ast.unparse(n.func)
            if fn in ("print", "builtins.print"):
                print_calls += 1
            elif fn in ("time.sleep", "sleep"):
                sleep_calls += 1
            elif fn.startswith("os.getenv") or fn.startswith("os.environ.get"):
                env_reads += 1
            elif fn.startswith("subprocess."):
                subprocess_calls += 1

# module-level assignments only (top-level body)
mod_globals: list[dict[str, Any]] = []
for f in iter_py():
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            val = n.value
            names = [t.id for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                     if isinstance(t, ast.Name)]
            if isinstance(val, (ast.List, ast.Dict, ast.Set)):
                mod_globals.append({"file": str(f), "line": n.lineno, "code": ast.unparse(n)[:120]})

out: dict[str, Any] = {
    "summary": {
        "files": len([x for x in file_stats if "loc" in x]),
        "loc": sum(x.get("loc", 0) for x in file_stats),
        "code_lines": sum(x.get("code", 0) for x in file_stats),
        "comment_lines": sum(x.get("comments", 0) for x in file_stats),
        "docstring_lines": sum(x.get("docstrings", 0) for x in file_stats),
        "funcs": total_defs,
        "untyped_funcs": untyped_defs,
        "classes": len(classes),
        "print_calls": print_calls,
        "env_reads": env_reads,
        "sleep_calls": sleep_calls,
        "subprocess_calls": subprocess_calls,
        "global_stmts": global_stmts,
    },
    "except": excs.most_common(),
    "top_funcs_length": sorted(funcs, key=lambda x: -x["length"])[:40],
    "top_funcs_complex": sorted(funcs, key=lambda x: -x["complexity"])[:30],
    "top_funcs_nest": sorted(funcs, key=lambda x: -x["nest"])[:15],
    "top_funcs_params": sorted(funcs, key=lambda x: -x["params"])[:20],
    "top_classes": sorted(classes, key=lambda x: -x["length"])[:20],
    "big_files": sorted(file_stats, key=lambda x: -x.get("loc", 0))[:25],
    "mod_globals": mod_globals,
    "funcs_over_200": [f for f in funcs if f["length"] > 200],
    "funcs_over_100": len([f for f in funcs if f["length"] > 100]),
}
OUT_PATH = Path("tmp/py-audit.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

s = out["summary"]
print("files", s["files"], "loc", s["loc"], "code", s["code_lines"], "comments", s["comment_lines"],
      "docstrings", s["docstring_lines"], "funcs", s["funcs"], "untyped", s["untyped_funcs"])
print("comment+demo ratio: %.1f%%" % (100 * (s["comment_lines"] + s["docstring_lines"]) / max(1, s["code_lines"])))
print("print", s["print_calls"], "env", s["env_reads"], "sleep", s["sleep_calls"],
      "subproc", s["subprocess_calls"], "globals", s["global_stmts"])
print("except:", out["except"][:12])
print("\n== longest funcs ==")
for f in out["top_funcs_length"][:25]:
    print(f"{f['length']:5d} L  c={f['complexity']:4d} n={f['nest']} p={f['params']:2d}  {f['file']}:{f['line']} {f['name']}")
print("\n== most complex ==")
for f in out["top_funcs_complex"][:20]:
    print(f"{f['complexity']:5d} c  {f['length']:5d} L  {f['file']}:{f['line']} {f['name']}")
print("\n== biggest classes ==")
for c in out["top_classes"][:15]:
    print(f"{c['length']:5d} L  m={c['methods']:3d}  {c['file']}:{c['line']} {c['name']}({','.join(c['bases'])})")
print("\n== funcs > 200 lines:", len(out["funcs_over_200"]), " > 100 lines:", out["funcs_over_100"])
print("\n== module-level mutable containers ==")
for g in out["mod_globals"][:40]:
    print(f"{g['file']}:{g['line']}  {g['code']}")
print("\n== big files ==")
for f in out["big_files"][:15]:
    print(f"{f['loc']:5d} L  {f['bytes']//1024:5d}KB  {f['file']}")
