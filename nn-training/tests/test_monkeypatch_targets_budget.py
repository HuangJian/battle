"""G19：测试面「私有名 monkeypatch」的次数**只减不增**（`python.refactor.md` §P0-8 第四项）。

## 为什么需要这道闸

P0-8 的原话：`monkeypatch` 意味着**几乎没有依赖注入** —— 所有协作方都是模块级函数/常量，
测试只能「改模块里的名字」。其中**改私有名**最贵：它把实现细节变成事实 API，重构一次要改
几百个用例，于是「重构」只能退化成「搬文件但保持形状」（S 系列 23 刀就是这么变成混入的）。

§P0-8 第 1 项（端口对象 / `seams`）铺开之前，这批存量没法一次性迁完；但没有闸的债会继续
长高。这道闸只做一件事：**冻结现状，新增即红**。

## 口径

* 计数 = AST 里 `monkeypatch.setattr(...)` / `monkeypatch.delattr(...)` 且目标名是私有：
  - 形如 `monkeypatch.setattr(obj, "_x", …)`：第二个实参是字符串字面量 `_x`；
  - 形如 `monkeypatch.setattr("pkg.mod._x", …)`：第一个实参是点分字符串，末段 `_x`。
* **dunder 不算**（`__enter__` 一类是语言协议，不是实现细节）；非字面量目标也不算
  （`name` 变量无法静态判断）。
* 扫描面 = `tests/**` + `e2e/**`。
* 基线在 `tests/golden/monkeypatch_private_baseline.json`（按文件冻结），只拦增长；
  另有一条「基线只许调小」防额度回流（与 G3/G4/G6/G9/G13/G14 同形）。

## 新代码怎么办

新用例打到私有名之前，先问：这个私有名是否应该有一个**公开读数**（`*_view()` /
`*_state()` / 端口对象的方法）？本仓已有的几十个读数就是这么长出来的（见
`docs/nn/engineering.md` §172–§178）。确实必须打桩的第三方边界（`time` / `os.environ` /
`urlopen`）通常打的是**公开名**，不在本闸口径里。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

from tests.helpers import source_scan
from tests.helpers.budget import sanity_floor

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "monkeypatch_private_baseline.json"

#: 扫描面：测试与 e2e（两者共用同一套 fixture 语义）。
FACE = ("tests", "e2e")


def _iter_face_py() -> list[Path]:
    return [p for pkg in FACE for p in sorted((ROOT / pkg).rglob("*.py"))]


def _is_private_name(text: str) -> bool:
    """点分名字的末段是否私有（dunder 不算：它是语言协议）。"""
    name = text.rsplit(".", 1)[-1]
    return name.startswith("_") and not name.startswith("__")


def _count_private_targets(src: str) -> int:
    """一次 AST 里「monkeypatch 打私有名」的次数（两种寻址形态都认）。"""
    n = 0
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        func = node.func
        if func.attr not in ("setattr", "delattr"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "monkeypatch"):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            n += _is_private_name(first.value)
            continue
        second = node.args[1] if len(node.args) >= 2 else None
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            n += _is_private_name(second.value)
    return n


@cache
def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in _iter_face_py():
        # 廉价预筛：计数只认 `monkeypatch.setattr/delattr(...)` ⇒ 源码里必有这个名字。
        # （2026-11-20 实测本段 2.6s → 1.1s，判据不变。）
        src = source_scan.read_text(str(p), "replace")
        if "monkeypatch" not in src:
            continue
        n = _count_private_targets(src)
        if n:
            counts[p.relative_to(ROOT).as_posix()] = n
    return counts


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_private_monkeypatch_targets() -> None:
    actual = _scan()
    baseline = _baseline()
    grown = [
        f"  {rel}: {n} 处（基线 {baseline.get(rel, 0)}）"
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    ]
    assert not grown, (
        "测试面新增了「monkeypatch 打私有名」：\n"
        + "\n".join(grown)
        + "\n  先问这个私有名该不该有公开读数（`*_view()` / `*_state()` / 端口对象的方法）；"
        "确实必须打桩的第三方边界通常打的是公开名，不在本闸口径里。"
    )


def test_baseline_only_shrinks() -> None:
    """打得少了必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan()
    baseline = _baseline()
    stale = [
        f"  {rel}: 基线 {base}，实际 {actual.get(rel, 0)}"
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    ]
    assert not stale, (
        "以下文件的私有 monkeypatch 已经减少，但基线还是旧数字：\n"
        + "\n".join(stale)
        + "\n  改成实际值（归零的条目直接删掉），让额度不再回流。"
    )


def test_scan_floor() -> None:
    """扫描入口自检：AST 没数到东西时立刻红（与 G14 同形）。"""
    total = sum(_scan().values())
    assert total >= sanity_floor(_baseline()), f"只数到 {total} 处私有 monkeypatch，扫描入口可能坏了"


def test_scanner_counts_only_private_targets() -> None:
    """口径自检：两种寻址形态都认；公开名 / dunder / 非字面量不认。"""
    src = (
        "monkeypatch.setattr(mod, '_a', 1)\n"
        "monkeypatch.setattr(mod, 'b', 1)\n"
        "monkeypatch.setattr(mod, '__enter__', 1)\n"
        'monkeypatch.setattr("pkg.mod._d", 1)\n'
        'monkeypatch.delattr("pkg.mod.e", raising=False)\n'
        "monkeypatch.setattr(mod, name, 1)\n"
        "other.setattr(mod, '_f', 1)\n"
    )
    assert _count_private_targets(src) == 2
