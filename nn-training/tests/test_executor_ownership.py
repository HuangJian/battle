"""G16：线程池**只能**由 `common.runtime.Runtime` 创建（`python.refactor.md` §P1-6 / B6）。

## 为什么

2026-11-17 量到生产面 10 处 `with ThreadPoolExecutor(...) as ex:` 就地建池：没有所有者
（池的生死绑在词法作用域上）、没有上限（`min(8, …)` 两处、`n_slots` 一处，叠加无人报数）、
没有取消点（`halt_event` 到不了池边）、10 处里只有 1 处写了 `thread_name_prefix`
（其余在 `py-spy` 里都是 `ThreadPoolExecutor-3_0`）。`Runtime` 把四件事收进一个对象后，
这条闸的任务就是**让它保持唯一入口** —— 否则下一个人「顺手 `with ThreadPoolExecutor`」
就把四件事一起长回来，而测试仍然全绿。

零容忍、无基线：唯一的合法住处是 `common/runtime.py` 自己。

## 口径

* AST 扫 `ThreadPoolExecutor(...)` / `ProcessPoolExecutor(...)` 调用（`cf.X` / `futures.X` /
  裸 `X` 三种写法），违规 = 出现在 `common/runtime.py` 以外。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`；`tools/` 与 `worker/scripts/`
  与 G3/G4/G14/G15 同款豁免（一次性脚本不是产品面）。
* `Runtime(...)` 必须给 `scope=`：线程名与读数都靠它，匿名 Runtime 等于把这条闸的收益让回去。
* 一条**使用量下限**：生产面 `Runtime(` 少于 8 处 = 迁移被无声回退（或扫描入口坏了）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.helpers import source_scan

ROOT = Path(__file__).resolve().parents[1]

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G14/G15 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")

#: 唯一允许就地建线程池的地方。
HOME = "common/runtime.py"

#: 迁移之后生产面应该有的 `Runtime(` 处数（只许更多；回退会掉到 0）。
MIN_RUNTIME_USES = 8

_POOLS = frozenset({"ThreadPoolExecutor", "ProcessPoolExecutor"})


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        for p in sorted((ROOT / pkg).rglob("*.py")):
            if "scripts" in p.parts:
                continue
            out.append(p)
    return out


def _called_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _pool_calls(src: str) -> list[int]:
    """裸建线程池的行号（三种写法都认）。"""
    return [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and _called_name(node) in _POOLS
    ]


def _anonymous_runtimes(src: str) -> list[int]:
    """没给 `scope=` 的 `Runtime(...)` 行号。"""
    return [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and _called_name(node) == "Runtime"
        and not any(k.arg == "scope" for k in node.keywords)
    ]


def test_only_the_runtime_builds_pools() -> None:
    offenders: list[str] = []
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        if rel == HOME:
            continue
        # 廉价预筛：两个池类名都含 `Executor`（2026-11-20 实测本文件 3 条从 ~4.5s 降到 ~0.6s）。
        text = source_scan.read_text(str(p), "replace")
        if "Executor" not in text:
            continue
        for line in _pool_calls(text):
            offenders.append(f"  {rel}:{line}")
    assert not offenders, (
        "这些地方绕过 `common.runtime.Runtime` 直接建了线程池（没有所有者/上限/取消点/线程名）：\n"
        + "\n".join(offenders)
        + '\n  改成 `with Runtime(scope="<谁>") as rt:` + `rt.map(...)` / `rt.pool("名字")`。'
    )


def test_every_runtime_names_its_scope() -> None:
    offenders: list[str] = []
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        text = source_scan.read_text(str(p), "replace")
        if "Runtime" not in text:
            continue
        for line in _anonymous_runtimes(text):
            offenders.append(f"  {rel}:{line}")
    assert not offenders, (
        "`Runtime(...)` 没给 `scope=`（线程名与 `describe()` 都靠它）：\n" + "\n".join(offenders)
    )


def test_the_migration_is_still_there() -> None:
    """两向护栏：闸在、但代码全回退了也是红的（否则这条闸只保护一个空承诺）。"""
    used = 0
    for p in _iter_face_py():
        if p.relative_to(ROOT).as_posix() == HOME:
            continue
        text = source_scan.read_text(str(p), "replace")
        if "Runtime" not in text:
            continue
        tree = ast.parse(text, filename=str(p))
        used += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _called_name(node) == "Runtime"
        )
    assert used >= MIN_RUNTIME_USES, (
        f"生产面只找到 {used} 处 `Runtime(`（期望 ≥{MIN_RUNTIME_USES}）—— "
        "迁移被无声回退了，或者扫描入口坏了。"
    )


def test_scan_counts_all_spellings_and_ignores_lookalikes() -> None:
    """口径自检：`cf.X` / `futures.X` / 裸 `X` 都算；`Executor` 变量与字符串不算。"""
    src = (
        "import concurrent.futures as cf\n"
        "cf.ThreadPoolExecutor(max_workers=1)\n"
        "futures.ProcessPoolExecutor()\n"
        "ThreadPoolExecutor(max_workers=2)\n"
        "ex = make_executor()\n"
        "Runtime(scope='x')\n"
        "Runtime()\n"
    )
    assert _pool_calls(src) == [2, 3, 4]
    assert _anonymous_runtimes(src) == [7]
