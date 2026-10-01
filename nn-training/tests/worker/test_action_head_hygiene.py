"""action-head hygiene（plan/new-era-stop.plan.md #6）。

三件机器可复验的事：

1. 动作维度 = `common.schema.MOVE_DIM`（5）+ `FIRE_DIM`（2）= `MASK_DIM`（7）；
2. `worker/models/rl_model.py` 是**死文件**——生产树（`common` / `remote` / `hub` / `worker` /
   `trainer` / `biz`）任何模块都不得 import 它
   （历史上曾改这个死文件改 `TOTAL_ACTION_DIM`，线上策略纹丝不动）；
3. 线上 move 头 `worker/models/student.py::PPOStudent` 从 `common.schema.MOVE_DIM` 取值，
   不写字面量。

纯源码扫描 + stdlib，零 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import FIRE_DIM, MASK_DIM, MOVE_DIM


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
    # 2026-09-30（刀 4）：`biz/` 是 `rl/` 的纯逻辑半（搬家前就在这条扫描面里）——
    # 不补上，扫描面就静默缩水 64 个模块（全仓最典型的那种「搬家把守卫搬瞎」）。
    # 2026-09-30（刀 6）：扫描面 = **生产树的全部包 + 根下入口脚本**，按目录枚举而不是手写包名 ——
    # 手写清单正是本文件最典型的哑故障（刀 3/刀 6 把 `ppo/` `models/` 搬进 `worker/` 后，
    # 旧清单里的 `ppo` 成了空目录 ⇒ 扫描面静默缩水）。
    pkgs = [d for d in ROOT.iterdir() if d.is_dir() and (d / "__init__.py").is_file()]
    pkgs = [d for d in pkgs if d.name not in {"tests", "e2e"}]
    files = [p for pkg in pkgs for p in pkg.rglob("*.py")] + list(ROOT.glob("*.py"))
    assert len(files) > 100, f"扫描面只有 {len(files)} 个文件——包被搬走/改名了？"
    for p in files:
        if _imports_rl_model(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], f"live pipeline import 了死文件 rl_model: {offenders}"


def test_live_head_is_built_from_schema_move_dim() -> None:
    """move 头必须取 common.schema.MOVE_DIM——字面量会让 dims 变更漏改而静默错配。"""
    src = (ROOT / "worker" / "models" / "student.py").read_text(encoding="utf-8")
    assert "nn.Linear(head_hidden, MOVE_DIM" in src
