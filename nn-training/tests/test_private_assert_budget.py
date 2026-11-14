"""G5：`tests/**` 不得新增「私有断言」（`python.refactor.md` §8、§P0-8）。

口径：AST 里每一条 `assert` 语句，只要判据表达式里出现**私有属性**（`x._private`，不含
`__dunder__`）或**私有键下标**（`d["_private"]`，生产侧常见的是 `dict` 契约），就算一条私有断言。
存量按文件冻结在 `tests/golden/private_assert_baseline.json`：只拦增长，另加「基线只许调小」，
避免额度回流。本文件自身的断言不读任何私有名 ⇒ 不占额度。

为什么需要这条：656 处 `assert ... ._private` 把实现细节钉成了事实 API，一次重构要改几百个用例，
于是「重构」只能变成「搬文件但保持形状」（§P0-8）。门禁不要求清算存量，但必须拦住增长，
否则接缝（`seams.py`）永远建不起来。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "private_assert_baseline.json"


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _touches_private(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            if sub.attr.startswith("_") and not _is_dunder(sub.attr):
                return True
        elif isinstance(sub, ast.Subscript):
            key = sub.slice
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value.startswith("_")
                and not _is_dunder(key.value)
            ):
                return True
    return False


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        n = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert) and _touches_private(node.test)
        )
        if n:
            out[p.relative_to(ROOT).as_posix()] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_private_assertions() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 条（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "新增了读私有状态的断言：\n"
        f"{detail}\n"
        "  优先断言可观察行为或公开查询（`seams.py` 里的工厂/查询函数）；"
        "确实是契约的私有量，先把它提升成公开查询再断言。"
    )


def test_baseline_only_shrinks() -> None:
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的私有断言已经减少，但 `tests/golden/private_assert_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（归零的条目删掉），额度才会真正下降。"
    )


def test_scan_sees_the_known_surface() -> None:
    baseline = _baseline()
    actual = _scan()
    assert baseline.get("tests/remote/test_rollout_node.py", 0) > 0
    assert actual.get("tests/worker/test_rollout_volume.py", 0) > 0
    assert sum(actual.values()) >= 500, "私有断言计数异常偏低，扫描面可能坏了"
    assert len(baseline) >= 100


def test_public_assertions_are_not_counted() -> None:
    """口径自检：公开属性、公开键、`__dunder__` 都不算。"""
    tree = ast.parse(
        "assert job.state == 'done'\n"
        "assert payload['status'] == 'ok'\n"
        "assert len(x.__dict__) == 0\n"
        "assert obj._private == 1\n"
        "assert payload['_secret'] == 'ok'\n"
    )
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert) and _touches_private(node.test)
    ]
    assert len(hits) == 2
