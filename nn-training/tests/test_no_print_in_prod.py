"""G3：生产包不得新增 `print()`（`python.refactor.md` §8）。

存量按**文件计数**冻结在 `tests/golden/print_baseline.json`：只拦增长，不要求一次清零。
口径是 AST 里的 `print(...)` 调用（注释/文档串里提到 print 不算）；`tools/` 与
`worker/scripts/` 是一次性脚本，按文档豁免。`tests/` 不在扫描面内。

为什么需要这条：日志体系迁移（P0-6）已经允许存量存在，但如果没有门禁，新代码会继续
用 `print` 绕过级别/结构化输出，迁移永远追不上新增。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "print_baseline.json"

#: 生产包；`tools/` 不在内，`worker/scripts/` 在下方按前缀豁免。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            n = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            )
            if n:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_print_calls() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了 `print()`：\n"
        f"{detail}\n"
        "  改用 `common.log` / 既有 logger（P0-6）；确属一次性工具的，放到 tools/ 或 "
        "worker/scripts/ 下，而不是放宽本基线。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际扫描确实命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    assert baseline.get("hub/boot.py", 0) > 0
    assert baseline.get("common/log.py", 0) > 0
    assert actual.get("hub/boot.py", 0) > 0, "扫描入口坏了（AST 没数到已知 print）"
    assert len(baseline) >= 50
    assert not any(rel.startswith(EXEMPT_PREFIXES) for rel in baseline)
    assert not any(rel.startswith("tools/") for rel in baseline)
