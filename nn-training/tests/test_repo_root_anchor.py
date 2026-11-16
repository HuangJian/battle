"""G12：仓根锚点**只有一份**（`common/paths.py`），生产面不许自己数层数。

出处：`python.refactor.md` §1 度量 + §10 B8 的第一刀。`REPO_ROOT` 从前住在
`common/distribution.py`，同时还有 13 份副本散在 `biz/ hub/ remote/ trainer/ worker/`
（`hub/store_offline.py` 那次 `parents[3]` → `parents[2]` 就是活例：层数算错**不报错**，
只是路径全指到仓库的父目录）。2026-11-16 这些副本全部改 import `common.paths.REPO_ROOT`。

留下的 19 处是**两种合法例外**，按文件冻结（只许调小）：

1. **入口脚本自举**（`train`, `trainer/eval_*_once.py`, `worker/eval_replays_once.py`,
   `worker/ppo/bench.py`, `worker/models/*`, `worker/data/*`, `worker/ppo/{engine,goal,intent}.py`,
   `worker/train/bc.py`）：它们要**在 import `common` 之前**算出根来插 `sys.path`
   （`worker/models/*` 等 `_sys.path.insert(0, str(_Path(__file__)…parent.parent.parent))`），
   那种鸡生蛋只能在本地算；写错会当场 ImportError，不是静默类错误。
2. `worker/models/{goal,intent}_net.py` 的模块级 `REPO_ROOT` 其实是 **nn-training 根**
   （历史命名，非仓根），它们只写 `nn-training/tmp/`；要改的是命名/归属，不是这里数层数。

判据是**文本**（`Path(__file__).resolve().parents[2]` 等三种写法）：AST 里这三行长得一样，
而文本扫描能连注释一起看——注释里引用旧写法是这份债的主要传播方式。`tools/`（脚本）与
`worker/scripts/`（与 G3 同豁免）不扫。
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 生产包；与 G3 / G4 / G9 / G11 同一扫描面。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 唯一合法定义处。
HOME = "common/paths.py"

#: 三种写法：`parents[2]` / `.parent.parent.parent` / `os.path.dirname` 三连。
_PAT = re.compile(
    r"Path\(__file__\)\.resolve\(\)\.parents\[2\]"
    r"|Path\(__file__\)\.resolve\(\)\.parent\.parent\.parent"
    r"|os\.path\.dirname\(os\.path\.dirname\(os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\)\)"
)

#: 债务表：文件 → 处数（理由见模块 docstring 的两种例外）。只许减，删条目即清零。
BASELINE: dict[str, int] = {
    "train/train_bc_v6.py": 1,
    "trainer/eval_a_once.py": 1,
    "trainer/eval_course_once.py": 1,
    "trainer/eval_m1_once.py": 1,
    "worker/data/dataset.py": 1,
    "worker/data/weights_io.py": 1,
    "worker/eval_replays_once.py": 1,
    "worker/models/core.py": 1,
    "worker/models/goal_net.py": 2,
    "worker/models/intent_net.py": 2,
    "worker/models/rl_model.py": 1,
    "worker/models/student.py": 1,
    "worker/ppo/bench.py": 1,
    "worker/ppo/engine.py": 1,
    "worker/ppo/goal.py": 1,
    "worker/ppo/intent.py": 1,
    "worker/train/bc.py": 1,
}


@cache
def _counts() -> dict[str, int]:
    """`{文件: 处数}` —— 生产面里自己数仓根层数的位置（不含 `common/paths.py`）。"""
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel == HOME or rel.startswith(EXEMPT_PREFIXES):
                continue
            n = len(_PAT.findall(p.read_text(encoding="utf-8", errors="replace")))
            if n:
                out[rel] = n
    return out


@cache
def _growth() -> dict[str, tuple[int, int]]:
    """`{文件: (现在, 基线)}` —— 超出基线的（新文件算「基线 0」）。"""
    base = _counts()
    return {rel: (n, BASELINE.get(rel, 0)) for rel, n in base.items() if n > BASELINE.get(rel, 0)}


@cache
def _stale() -> dict[str, tuple[int, int]]:
    """`{文件: (基线, 现在)}` —— 已经少于基线了，表还留着旧数（额度会回流）。"""
    now = _counts()
    return {rel: (n, now.get(rel, 0)) for rel, n in BASELINE.items() if now.get(rel, 0) < n}


def test_repo_root_is_defined_once() -> None:
    """`common/paths.py` 只有一份；其余生产面不许自己数层数。"""
    grown = _growth()
    detail = "\n".join(
        f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in sorted(grown.items())
    )
    assert not grown, (
        "生产面又出现了「自己数仓根层数」的代码（`python.refactor.md` §10 B8）：\n"
        f"{detail}\n"
        f"  改成 `from common.paths import REPO_ROOT`（{HOME} 是唯一一份定义）。"
        "入口脚本要在 import common 之前插 sys.path 的话，把该文件加进 BASELINE 并写明理由。"
    )


def test_repo_root_baseline_only_shrinks() -> None:
    """基线条目只许减；已经改干净的条目要删掉（否则额度回流）。"""
    stale = _stale()
    detail = "\n".join(
        f"  {rel}: 基线 {base} 处，现在 {n if n else '没有'}"
        for rel, (base, n) in sorted(stale.items())
    )
    assert not stale, (
        "以下文件已经不再（或少于）自己数仓根层数，但 BASELINE 还是旧数：\n"
        f"{detail}\n"
        "  把 BASELINE 改成实际处数；清零了就删掉条目。"
    )
