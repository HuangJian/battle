"""函数体内的 12 行归一克隆窗口（B8 的两条守卫共用）。

口径与 `tools/audit/py_clones.py` 同源：剔空行与纯注释行、行内空白归一。差别只有一处——
**只收函数体内的行**：模块序言（`import` 块 / docstring）在结构上必然相同（两个 PPO 头就
必须 import 同一批东西），把它算成「重复」只会让守卫变成噪声，而债务从来不在 import 里。

用法：

    a = function_windows(Path("worker/ppo/goal.py"))
    b = function_windows(Path("worker/ppo/intent.py"))
    assert not (set(a) & set(b))     # 或按所属函数名放过有意保留的 `main`

返回值：`{归一后的 12 行: (所属函数名, 起始行号)}`。
"""

from __future__ import annotations

import ast
from pathlib import Path

WIN = 12


def function_windows(path: Path) -> dict[tuple[str, ...], tuple[str, int]]:
    """`path` 里所有「函数体内、12 行、归一后逐字相同」的窗口。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    owners: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for row in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owners[row] = node.name
    rows = [
        (i, " ".join(line.split()), owners[i])
        for i, line in enumerate(src.splitlines(), 1)
        if line.strip() and not line.strip().startswith("#") and i in owners
    ]
    out: dict[tuple[str, ...], tuple[str, int]] = {}
    for i in range(len(rows) - WIN + 1):
        key = tuple(r[1] for r in rows[i : i + WIN])
        out.setdefault(key, (rows[i][2], rows[i][0]))
    return out
