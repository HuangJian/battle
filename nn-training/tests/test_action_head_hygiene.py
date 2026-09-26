"""action-head hygiene（plan/new-era-stop.plan.md #6）。

三件机器可复验的事：

1. 动作维度 = `schema.MOVE_DIM`（5）+ `FIRE_DIM`（2）= `MASK_DIM`（7）；
2. `models/rl_model.py` 是**死文件**——`rl/` / `ppo/` / `remote/` 任何模块都不得 import 它
   （历史上曾改这个死文件改 `TOTAL_ACTION_DIM`，线上策略纹丝不动）；
3. 线上 move 头 `models/student.py::PPOStudent` 从 `schema.MOVE_DIM` 取值，
   不写字面量。

纯源码扫描 + stdlib，零 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import FIRE_DIM, MASK_DIM, MOVE_DIM


def test_action_dims_from_schema() -> None:
    assert MOVE_DIM == 5
    assert FIRE_DIM == 2
    assert MASK_DIM == MOVE_DIM + FIRE_DIM == 7


def _imports_rl_model(text: str) -> bool:
    """True only for a real import line (docstring mentions don't count)."""
    for line in text.splitlines():
        s = line.strip()
        if (s.startswith("import ") or s.startswith("from ")) and "rl_model" in s:
            return True
    return False


def test_live_pipeline_never_imports_the_dead_teacher_model() -> None:
    offenders: list[str] = []
    files = [p for pkg in ("rl", "ppo", "remote") for p in (ROOT / pkg).rglob("*.py")]
    files += [ROOT / "models" / n for n in ("student.py", "core.py")]
    for p in files:
        if _imports_rl_model(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], f"live pipeline import 了死文件 rl_model: {offenders}"


def test_live_head_is_built_from_schema_move_dim() -> None:
    """move 头必须取 schema.MOVE_DIM——字面量会让 dims 变更漏改而静默错配。"""
    src = (ROOT / "models" / "student.py").read_text(encoding="utf-8")
    assert "nn.Linear(head_hidden, MOVE_DIM" in src
