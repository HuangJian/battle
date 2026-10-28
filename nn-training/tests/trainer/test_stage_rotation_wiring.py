"""tests/trainer/test_stage_rotation_wiring.py —— 三条腿**同一个窗口**（评审 A3 的守门用例）。

plan/it-stage-ratation.plan.md §3.5 / 判据 3。为什么这条用例必须存在：窗口有三个消费者，
分属两条链（导出计划 → 云机采集；本机连续配额；打包前裁剪 + PPO 装载截断）。它们**各自**
按 `--stages` 反解过 —— 采集按 12 关、裁剪按 35 关 ⇒ 约 2/3 的样本被裁掉，而且日志只会说
「丢 N 局未传」。行为面看不出错，只能靠「同源」这条断言钉住：

    _per_stage_quota(it) == volume_block(窗口).per_stage_quota == ceil(target/窗口关数)

另钉两条**纪律**：老行为（无 rotation）逐字节不变；旧门控（`--rotate-stages`）仍响亮拒收
（它是另一套 pair 构造，与 volume 的按关配额混用无解）。
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from trainer.loop_steps import TrainingSteps
from trainer.loop_volume import TrainingVolume
from worker.stage_rotation import window_from_args
from worker.volume_waves import parse_stages_arg, validate_volume_block, volume_block

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
        # 旧门控（必须为空/0 —— 新窗口与它们互斥）
        "curriculum_stages": "",
        "rotate_stages": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Quota(TrainingSteps):
    """最小宿主：`self.args` + `self._rotate_seed`（窗口键与种子流同源）。"""

    def __init__(self, **over: object) -> None:
        self.args = _args(**over)
        self._rotate_seed = int(getattr(self.args, "rotate_seed", 0) or 0)


class _Vol(TrainingVolume):
    """最小宿主：`_volume_stages` 只读 `self.args` / `self._rotate_seed`。"""

    def __init__(self, **over: object) -> None:
        self.args = _args(**over)
        self._rotate_seed = int(getattr(self.args, "rotate_seed", 0) or 0)
        self._volume_est = 0


def test_per_stage_quota_follows_the_window() -> None:
    """★ 判据 3：裁剪/PPO 截断用的配额与**块里钉的**配额逐字节相同（按窗口，不是全集）。

    旧形状：`target/35 = 35`；窗口 12 关 ⇒ `target/12 = 100`。两者差 2.86× —— 按旧的裁，
    云机采到的 100/关 只剩 35/关，模式 1 的全部收益（每关密度 ×2.92）被吃掉。
    """
    args = _args()
    window = window_from_args(args, 5, parse_stages_arg(FULL))
    assert len(window) == 12

    expect = math.ceil(TARGET / len(window))
    assert _Quota()._per_stage_quota(5) == expect

    block = volume_block(args, est_samples_per_game=0, stages_override=window)
    assert block is not None
    assert block["per_stage_quota"] == expect
    # 计划侧的形状校验（同源等式）也必须认这个块
    assert validate_volume_block(block)["per_stage_quota"] == expect


def test_without_rotation_the_quota_is_byte_identical() -> None:
    """无 rotation ⇒ 分母仍是全集（`ceil(1200/35)=35`），老课程一行不差。"""
    stub = _Quota(rotation_mode="", rotation_per_it=0)
    assert stub._per_stage_quota(5) == math.ceil(TARGET / 35)
    block = volume_block(stub.args, est_samples_per_game=0)
    assert block is not None and block["per_stage_quota"] == math.ceil(TARGET / 35)


def test_volume_stages_returns_the_window_for_the_round() -> None:
    """本机连续配额链的关集 = 同一窗口（配额/硬顶/unmet/分关账全部自动跟着它走）。"""
    vol = _Vol()
    w = vol._volume_stages(7)
    assert w == window_from_args(vol.args, 7, parse_stages_arg(FULL))
    assert len(w) == 12 and w == sorted(w)
    # 换一轮就换窗口（不是固定子集）
    assert vol._volume_stages(8) != w


def test_legacy_rotate_stages_is_still_refused() -> None:
    """旧门控仍响亮拒收：`--rotate-stages` 是 `build_pairs` 那一套，与按关配额混用无解。"""
    with pytest.raises(SystemExit, match="rotate-stages"):
        _Vol(rotate_stages=3)._volume_stages(1)
    with pytest.raises(SystemExit, match="curriculum-stages"):
        _Vol(curriculum_stages="0-9")._volume_stages(1)


def test_bad_rotation_config_is_a_system_exit_not_a_traceback() -> None:
    """pinned 里写了不在 `--stages` 的关号 ⇒ SystemExit（带可操作文案），不是 ValueError 冒泡。"""
    with pytest.raises(SystemExit, match="rotation 配置非法"):
        _Vol(rotation_mode="pinned", rotation_pinned="99")._volume_stages(1)


# ★ 2026-10-28：原先这里钉的是「轮换 + 多轮包 ⇒ 拒导」（评审 A4 / §3.7）。云机轮换落地后
#   拒收面换成「配方进计划、每轮按配方重解」——一条计划带一份块但每轮有自己的窗口，
#   用例在 `tests/worker/test_plan_rotation_multiround.py`（每轮窗口 / 同源 / 拒收）与
#   `tests/trainer/test_stage_rotation_export.py`（全关 est 表 + 多轮包不再拒）。
#   这里不再重钉旧文案：那个拒收点已经删了，继续断言「必须抛 SystemExit」只会把
#   已退役的行为焊回测试里。
