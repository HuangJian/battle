"""test_kick_once_paths — kick-once.py 的仓储根解析回归（2026-09-19）。

背景：`dashboard/src/evalboard/kick-once.py` 用 `Path(__file__).parents[1]` 当仓储根
——那是 2026-09-15 目录重组（tools/training/ → dashboard/）前的层数，之后
`ROOT/nn-training` 指向 `dashboard/src/nn-training`（不存在）⇒ 脚本一跑就
`ModuleNotFoundError`（人手兜底通道静默失效，而 route.ts 的提示还在让用户跑它）。

本用例是**失败先行的复现**：修前 `ROOT` 不含 nn-training，修后通过。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KICK = REPO_ROOT / "dashboard" / "src" / "evalboard" / "kick-once.py"


def _load_kick_once():
    spec = importlib.util.spec_from_file_location("kick_once_under_test", KICK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 只跑 import 与常量；main() 在 __main__ 守卫后
    return mod


def test_root_points_at_repo_root() -> None:
    mod = _load_kick_once()
    assert mod.ROOT == REPO_ROOT, f"kick-once ROOT={mod.ROOT} 不是仓储根 {REPO_ROOT}"


def test_nn_training_is_importable_from_root() -> None:
    mod = _load_kick_once()
    assert (mod.ROOT / "nn-training" / "rl" / "batch_eval.py").is_file()
    assert (mod.ROOT / "nn-training" / "pid_probe.py").is_file()
