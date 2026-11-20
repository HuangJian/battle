"""G14：生产面 `Any` 注解的次数**只减不增**（`python.refactor.md` §P1-4）。

## 为什么需要这道闸

`Any` 是类型系统里的 `eval()`：写下去那一刻什么都不用想，代价由半年后读代码的人付 ——
`dict[str, Any]` 的字段含义只能写在 docstring 里，改字段名编译器看不见（§9「注释替代类型」）。
审计量到 **1176 处**（182 个文件，P1-4 原文记的是 726，此后只涨不跌）。别的类型债都有闸
（`type: ignore` 走 G6、形状走 G1），唯独最能长的那一种没有；于是「先标 Any 跑通，回头再补」
永远排不上队。

## 为什么不是「`Any` → `object` 一把梭」

审计 §P1-4 的方案写着「把 `Any` 换成 `object`（惰性）」。2026-11-16 实测过这条：
生产面 78 个文件的 236 处 `dict[str, Any]` 批量改成 `dict[str, object]`，mypy 当场
**406 个红点 / 93 个文件** —— 取值真的被当 `Any` 用（`.get(k)` 之后直接算、直接取属性）。
说明这些 dict 缺的不是「更宽的注」，而是**字段表**：该写成 `TypedDict` / 具体协议。
所以这道闸不做自动改造，只负责让存量不再长大，下降留给逐个数据边界的类型化。

## 口径

* 计数 = AST 里名为 `Any` 的 `Name`（`x: Any` / `cast(Any, …)`）与属性 `*.Any`
  （`typing.Any` 写法）；注释 / docstring / 字符串里的字样天然不算。
* 只允许 `from typing import Any` 这一种引入形态 —— `Any as A` 会把上面两个判据一起绕过，
  由 `test_any_is_not_aliased` 单独拦。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`；`tools/` 与 `worker/scripts/`
  与 G3/G4/G12 同款豁免。
* 基线在 `tests/golden/any_baseline.json`（按文件冻结），只拦增长；另有一条「基线只许调小」
  防额度回流（与 G3/G4/G6/G9/G13 同形）。
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from pathlib import Path

from tests.helpers import source_scan
from tests.helpers.budget import sanity_floor

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "any_baseline.json"

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G12 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        for p in sorted((ROOT / pkg).rglob("*.py")):
            if "scripts" in p.parts:
                continue
            out.append(p)
    return out


def _is_any(node: ast.AST) -> bool:
    """`Any`（`x: Any`）或 `*.Any`（`typing.Any`）—— 两种写法都算，别名由下面的用例拦。"""
    if isinstance(node, ast.Name):
        return node.id == "Any"
    return isinstance(node, ast.Attribute) and node.attr == "Any"


def _count_any(src: str) -> int:
    """一次 AST 里 `Any` 的出现次数。"""
    return sum(1 for node in ast.walk(ast.parse(src)) if _is_any(node))


@cache
def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in _iter_face_py():
        # 廉价预筛：`Any` 的两种形态（`Any` / `*.Any`）都在源码里留这个标识符。
        # 373 个生产文件里 197 个提到它（2026-11-20 实测 1.8s → 1.1s）。
        text = source_scan.read_text(str(p), "replace")
        if "Any" not in text:
            continue
        n = _count_any(text)
        if n:
            counts[p.relative_to(ROOT).as_posix()] = n
    return counts


@cache
def _baseline() -> dict[str, int]:
    raw: dict[str, dict[str, int]] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return raw["any"]


def test_no_new_any() -> None:
    actual = _scan()
    baseline = _baseline()
    lines = [
        f"  {rel}: {n} 处（基线 {baseline.get(rel, 0)}）"
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    ]
    assert not lines, (
        "生产面新增了 `Any` 注解：\n"
        + "\n".join(lines)
        + "\n  数据边界请给**字段表**（`TypedDict` / frozen dataclass），不透明的句柄用 `object`；"
        "确实只能标 `Any` 的（第三方 API 返回、动态属性）加到"
        " `tests/golden/any_baseline.json` 对应文件上，并在提交信息里写清为什么 `object` 不行。"
    )


def test_baseline_only_shrinks() -> None:
    """标得少了必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan()
    baseline = _baseline()
    lines = [
        f"  {rel}: 基线 {base}，实际 {actual.get(rel, 0)}"
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    ]
    assert not lines, (
        "以下文件的 `Any` 已经减少，但 `tests/golden/any_baseline.json` 还是旧数字：\n"
        + "\n".join(lines)
        + "\n  改成实际值（归零的条目直接删掉），让额度不再回流。"
    )


def test_scan_floor() -> None:
    """扫描入口自检：AST 没数到东西（walk 坏了 / 包名改了）时立刻红。"""
    total = sum(_scan().values())
    assert total >= sanity_floor(_baseline()), f"只数到 {total} 处 Any，扫描入口可能坏了"


#: 「`Any` 被改名引入」的廉价预筛（`Any as A` 只有在 import 里才会出现）。
#: 全仓 1.8s 的全量解析里这一半本来是重复付的（`_scan()` 刚解析过同一批文件）。
_ANY_ALIAS_HINT = re.compile(r"\bAny\s+as\s+\w+")


def test_any_is_not_aliased() -> None:
    """`from typing import Any as A` 会让上面两个判据同时失效 —— 单独拦掉。"""
    offenders: list[str] = []
    for p in _iter_face_py():
        text = source_scan.read_text(str(p), "replace")
        if _ANY_ALIAS_HINT.search(text) is None:
            continue
        tree = ast.parse(text, filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for a in node.names:
                if a.name == "Any" and a.asname not in (None, "Any"):
                    offenders.append(f"  {p.relative_to(ROOT).as_posix()}: Any as {a.asname}")
    assert not offenders, (
        "`Any` 被改名引入（计数判据会被绕过）：\n" + "\n".join(offenders) + "\n  改成 `from typing import Any`。"
    )


def test_counts_only_real_annotations() -> None:
    """口径自检：注释 / docstring / 字符串里的字样不算；`*.Any` 与 `Any` 都算。"""
    src = (
        '"""文档里写 Any 不算。"""\n'
        "from typing import Any, cast\n"
        "x: Any = None\n"
        "y = 1  # Any 在注释里\n"
        'z = "Any"\n'
        "w = typing.Any\n"
        "v = cast(Any, 3)\n"
    )
    assert _count_any(src) == 3
