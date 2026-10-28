"""tests/trainer/test_stage_rotation_export.py —— 导出侧的**全关 est 表**与多轮包。

plan/it-stage-ratation.plan.md §9.5（2026-10-28）。导出侧要保证两件事：

  ① 开了轮换 ⇒ `volume` 块里带**全关** est 表（`est_s_all`）—— 云机跑到第 2 轮换到别的
     窗口时，要能按同一套反解算出那几关的局数；窗口外的关没有 est 就会静默回退全局单值
     （「每关同一个 G」的老病）。
  ② 不开轮换 ⇒ 块形状逐字节不变（不许因为这次改动让既有计划多出一个键）。

（多轮包本身不再被拒 —— 旧用例 `test_multi_round_bundle_with_rotation_is_refused` 随
§3.7 的拒收一起退役，改为在 `tests/worker/test_plan_rotation_multiround.py` 里钉
「每轮窗口 == 配方解出来的窗口」。）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_steps import TrainingSteps
from worker.stage_rotation import window_from_args
from worker.volume_waves import parse_stages_arg

FULL = "0-34"
TARGET = 1200


def _args(**over: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "mode": "per-tick",
        "target_transitions": TARGET,
        "stages": FULL,
        "rotation_mode": "even",
        "rotation_per_it": 12,
        "rotation_pinned": "",
        "rotate_seed": 4242,
        "est_agg": "latest",
        "max_games_per_stage": 0,
        # 旧门控（新窗口与它们互斥）
        "curriculum_stages": "",
        "rotate_stages": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Export(TrainingSteps):
    """最小宿主：`_volume_plan_block` 只读 `args` / `_rotate_seed` / `_traj_root`。"""

    def __init__(self, traj_root: Path, **over: object) -> None:
        self.args = _args(**over)
        self._rotate_seed = int(self.args.rotate_seed)
        self._traj_root = traj_root
        self._jsonl_path = None


def test_rotation_export_pins_the_all_stage_est_table(tmp_path: Path) -> None:
    """★ 开轮换 ⇒ `est_s_all` 覆盖**全关**；窗口块本身与派生口径同源。"""
    stub = _Export(tmp_path)
    block = stub._volume_plan_block(5)
    assert block is not None
    all_stages = parse_stages_arg(FULL)
    assert sorted(int(k) for k in block["est_s_all"]) == all_stages
    window = window_from_args(stub.args, 5, all_stages, rotate_seed=stub._rotate_seed)
    assert len(window) == 12
    assert block["stages"] == window
    assert block["per_stage_quota"] == -(-TARGET // len(window))
    # 窗口里的 est 就是全关表里那几关（同一个来源，不另算一份；写块时是 int 键，
    # 落成 JSON 后变字符串键 —— `volume_block_for_window` 两种都认）
    for s in window:
        assert block["est_s_by_stage"][s] == block["est_s_all"][s]


def test_without_rotation_the_block_is_byte_identical(tmp_path: Path) -> None:
    """未开轮换 ⇒ 块里没有 `est_s_all`（老计划逐字节不变）。"""
    stub = _Export(tmp_path, rotation_mode="", rotation_per_it=0)
    block = stub._volume_plan_block(5)
    assert block is not None
    assert block.get("est_s_all") is None
    assert block["stages"] == parse_stages_arg(FULL)


def test_exported_recipe_matches_the_args(tmp_path: Path) -> None:
    """`recipe_from_args` 与导出侧写进计划的配方同源（`mode`/`per_it`/`pinned`）。"""
    from worker.stage_rotation import recipe_from_args

    stub = _Export(tmp_path)
    assert recipe_from_args(stub.args) == {"mode": "even", "per_it": 12, "pinned": []}
    pinned = _Export(tmp_path, rotation_mode="pinned", rotation_per_it=10, rotation_pinned="20-22")
    assert recipe_from_args(pinned.args) == {"mode": "pinned", "per_it": 10, "pinned": [20, 21, 22]}
    # 缺席 ⇒ None（计划不落键）
    assert recipe_from_args(_Export(tmp_path, rotation_mode="").args) is None
