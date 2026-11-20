"""P1-10：生产包每一处 `torch.load` 都必须显式 `weights_only=True`。

torch 2.7 的默认值已经是 `True`，但把安全语义寄托在**版本默认值**上意味着一次降级
就是 pickle RCE。这里同时拦直接调用 `torch.load(...)` 与
`from torch import load` 后的 `load(...)`；`tools/` 是一次性脚本，不在生产面内。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")


def _torch_load_calls(tree: ast.AST) -> list[ast.Call]:
    aliases = {"torch"}  # `torch.load` 的模块名；直接调用另有别名表
    load_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "torch"
            and any(a.name == "load" for a in node.names)
        ):
            load_names.update(a.asname or a.name for a in node.names if a.name == "load")
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "load"
            and isinstance(f.value, ast.Name)
            and f.value.id in aliases
        ) or (isinstance(f, ast.Name) and f.id in load_names):
            calls.append(node)
    return calls


def test_every_production_torch_load_is_weights_only() -> None:
    bad: list[str] = []
    total = 0
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            for call in _torch_load_calls(tree):
                total += 1
                kw = {k.arg: k.value for k in call.keywords if k.arg}
                val = kw.get("weights_only")
                if not (isinstance(val, ast.Constant) and val.value is True):
                    bad.append(f"{rel}:{call.lineno}")
    assert total >= 8, f"扫描器没找到生产 torch.load（{total} 处）—— 口径可能坏了"
    assert not bad, (
        "生产 torch.load 缺少显式 `weights_only=True`（不要依赖 torch 版本默认值）：\n  "
        + "\n  ".join(bad)
    )
