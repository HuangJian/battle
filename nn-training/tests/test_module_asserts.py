"""G7：生产包不得用 `assert` 做**运行时校验**（`python.refactor.md` §P1-9）。

两半，判据不同：

1. **模块级 / 类体级 assert = 零容忍**。`python -O` 会把它们整段删掉，而它们校验的是
   「张量布局与 TS 侧逐字一致」「步骤表与实现一一对应」「评估池与 rollout 抽签区间不相交」
   这类**正确性根基** —— 优化级别不该改变正确性。2026-11-14 先清了 `common/schema.py` 的 5 处，
   2026-11-18 把扫描面从 `common/` 扩到**全部生产包**（`biz/ common/ hub/ remote/ train/
   trainer/ worker/`），扩面时抓到仅剩的两处：`trainer/loop_round_steps.py` 的步骤表同步与
   `worker/eval_track.py` 的种子池区间，都改成了 import 期的显式 `raise`（不是换一行注释）。
2. **函数体里的 assert = 冻结存量**（`tests/golden/assert_baseline.json`，62 处 / 33 文件）。
   P1-9 的裁定是「热路径的 assert 保留但加注释」，一次清零等于把它们全改成永远执行的
   `if … raise`（热路径代价 + 语义改变）。所以这一半只拦增长，与 G3/G4/G6/G9/G14 同一形态。

豁免面（与 G3 / G4 同一先例）：`worker/scripts/` 的一次性脚本与 `hub/smoke_loopback.py`
（冒烟脚本里 `assert` 就是结论本身）。`tests/` / `e2e/` / `tools/` 不在扫描面内。

第三条是同一节的后半句：**`python -O` 不得出现在任何可执行命令面**（`.sh` / `Makefile` /
`*.yml` / `package.json` 的 scripts）。上面两半只保住了 assert，这条保住「不靠优化级别
关掉校验」这个前提本身。

消抖口径（与 G4 相同）：基线不是豁免证，是债务表 —— 某文件计数降了就同步调小条目，
归零就删条目，`test_assert_baseline_only_shrinks` 会盯着。
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from pathlib import Path

import pytest

from common import schema
from tests.helpers import source_scan
from tests.helpers.budget import sanity_floor

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
BASELINE_PATH = ROOT / "tests" / "golden" / "assert_baseline.json"

#: 生产包；与 G3 / G4 / G9 / G14 同一扫描面。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
#: 一次性脚本（G3/G4 同款先例）。
EXEMPT_PREFIXES = ("worker/scripts/",)
#: 冒烟脚本：`assert` 是它的结论（P1-9 原文已认定为可接受）。
EXEMPT_FILES = ("hub/smoke_loopback.py",)

#: `python -O` / `python3 -OO` / `python3.10 -O` 形态（命令面文本扫描用）。
_OPTIMIZE_FLAG = re.compile(r"\bpython[0-9.]*\b[^\n|;&]*\s-O{1,2}\b")


class _ModuleAsserts(ast.NodeVisitor):
    """收集**模块作用域与类体**里的 assert（函数体里的不算 —— 那是第二半的存量面）。"""

    def __init__(self) -> None:
        self.scope = 0
        self.hits: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_Assert(self, node: ast.Assert) -> None:
        if self.scope == 0:
            self.hits.append(node.lineno)


def _prod_files() -> list[Path]:
    out: list[Path] = []
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES) or rel in EXEMPT_FILES:
                continue
            out.append(p)
    return sorted(out)


def _text(path: Path) -> str:
    """共享的文本缓存（同一次 pytest 进程里别的门禁也读同一份）；`errors="replace"` 与旧读法一致。"""
    return source_scan.read_text(str(path), "replace")


@cache
def _parse(path: Path) -> ast.Module:
    return source_scan.parse(str(path), "replace")


def _module_level_asserts() -> list[str]:
    bad: list[str] = []
    for p in _prod_files():
        # 廉价预筛：源码里连 `assert` 字样都没有 ⇒ 不可能有 Assert 节点（判据不变，
        # 2026-11-20 实测本段 3.1s → 0.5s）。
        if "assert" not in _text(p):
            continue
        v = _ModuleAsserts()
        v.visit(_parse(p))
        bad.extend(f"{p.relative_to(ROOT).as_posix()}:{ln}" for ln in v.hits)
    return bad


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in _prod_files():
        if "assert" not in _text(p):
            continue
        n = sum(1 for node in ast.walk(_parse(p)) if isinstance(node, ast.Assert))
        if n:
            out[p.relative_to(ROOT).as_posix()] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_module_level_asserts_in_production_packages() -> None:
    """`python -O` 下会消失的那一类：生产包零容忍（2026-11-18 扩面后）。"""
    bad = _module_level_asserts()
    assert not bad, (
        "生产包的模块级 / 类体级 assert 在 `python -O` 下会静默消失：\n  "
        + "\n  ".join(bad)
        + "\n  改成显式 `if not …: raise RuntimeError(...)`（`common/schema._check_layout` 是先例）。"
    )


def test_no_new_function_level_asserts() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了 assert：\n"
        f"{detail}\n"
        "  运行时校验用显式 `raise`（别依赖优化级别）；热路径的调试期 assert 才允许，"
        "且要真的降低某个入口的存量（基线是债务表，不是豁免证）。"
    )


def test_assert_baseline_only_shrinks() -> None:
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的 assert 已经减少，但 `tests/golden/assert_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（归零就删条目），让额度不再回流。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    assert sum(actual.values()) >= sanity_floor(baseline), (
        f"扫描只数到 {sum(actual.values())} 处（基线 {sum(baseline.values())}）—— AST 或扫描面坏了"
    )
    # 锚文件：这两个文件的 assert 是「热路径调试期校验」的代表，它们掉出扫描面说明 walk 坏了。
    assert {"worker/ppo/engine.py", "trainer/queue_worker.py"} <= set(actual), sorted(actual)
    leaked = {rel for rel in actual if rel.startswith(EXEMPT_PREFIXES) or rel in EXEMPT_FILES}
    assert not leaked, f"豁免面混进扫描结果：{sorted(leaked)}"


def _command_surfaces() -> list[Path]:
    """可执行命令面：跑生产/训练命令的地方（`.sh` / `Makefile` / CI / package scripts）。"""
    files = [*(REPO / "tools" / "githook").glob("*.sh"), ROOT / "Makefile", REPO / "package.json"]
    files += [*REPO.glob("*.sh"), *_pick(REPO / ".github" / "workflows")]
    return sorted(p for p in files if p.is_file())


def _pick(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [*directory.glob("*.yml"), *directory.glob("*.yaml")]


def test_python_optimize_flag_is_not_used_in_any_command() -> None:
    """`python -O` 会关掉 assert：不许出现在任何可执行命令面（这是 G7 两半的前提）。"""
    hits: list[str] = []
    for p in _command_surfaces():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _OPTIMIZE_FLAG.search(line):
                hits.append(f"{p.relative_to(REPO).as_posix()}:{i}: {line.strip()}")
    assert not hits, (
        "命令面出现了 `python -O`（会静默关掉全部 assert）：\n  "
        + "\n  ".join(hits)
        + "\n  生产/训练/门禁命令一律不带优化级别。"
    )
    # 扫描入口自检：真的扫到了门禁脚本与 CI（否则这条断言是空转）。
    scanned = {p.name for p in _command_surfaces()}
    assert {"pre-commit", "nn-python-gate.sh"} & scanned, sorted(scanned)


def test_schema_layout_check_survives_optimization() -> None:
    """显式 raise 与 assert 不同：不依赖解释器优化级别。"""
    with pytest.raises(RuntimeError, match="expected 2 entries"):
        schema._check_layout("probe", [(0, "only")], 2)
    with pytest.raises(RuntimeError, match=r"indices must be 0\.\.1"):
        schema._check_layout("probe", [(0, "a"), (0, "b")], 2)
