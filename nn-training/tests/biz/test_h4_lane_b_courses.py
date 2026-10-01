"""Wave 2（b 腿）起草课契约：三臂的**冻结项逐字一致** + 剂量档位正确（2026-09-30）。

为什么值得钉：Wave 1 的收官清单里记了**四处预注册偏离**（400 局没跑 / 单臂 sd 当配对 sd /
判据段与日常段混用 / 守卫表没数字）。预注册是契约，契约靠测试守——这门课一旦被后人静默改
（换个剂量、换 seed0、换口径），下面任何一条都会立刻红。

不钉的：注释文本之外的一切运行期行为（真开课/训练在控制台，不在这份测试的射程内）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biz.course_resolve import load_course
from biz.reward_validation import validate_reward

REPO = Path(__file__).resolve().parents[3]
CURRICULA = REPO / "nn-training" / "curricula"
ARMS = ("h4-lane-b0", "h4-lane-b1", "h4-lane-b2")
# 预注册剂量（DECISIONS §2026-09-30-goalnn-wave2-dose-ladder）：f = 7.5% / 15% killEV
WLANE = {"h4-lane-b0": None, "h4-lane-b1": 265.0, "h4-lane-b2": 529.0}


def _course(name: str):  # type: ignore[no-untyped-def]
    return load_course(CURRICULA / f"{name}.jsonc")


def _text(name: str) -> str:
    return (CURRICULA / f"{name}.jsonc").read_text(encoding="utf-8")


def test_all_three_arms_load_and_pass_reward_validation() -> None:
    for name in ARMS:
        report = validate_reward(_course(name).reward_spec())
        assert not report.errors, f"{name}: {report.errors}"  # errors 是 tuple（空 = 过）


def test_dose_is_frozen_per_arm() -> None:
    for name, wlane in WLANE.items():
        spec = _course(name).reward_spec()
        if wlane is None:  # 零臂：公式里没有 lane 项，参数表里也不该有 wLane
            assert "wLane" not in spec.params, name
            assert "onLaneMoveTicks" not in spec.formula, name
        else:
            assert spec.params["wLane"] == pytest.approx(wlane), name  # 改剂量 = 换判决条件
            assert "onLaneMoveTicks" in spec.formula, name
            assert "onLaneTicks" not in spec.formula, name  # 只罚移动（A1 几何，不是 A2 的 onLaneTicks）


def test_arms_share_everything_that_must_be_paired() -> None:
    """三臂唯一允许不同的字段 = 剂量 + 路径 + 课名；其余逐字同（配对前提）。"""
    cs = {name: _course(name) for name in ARMS}
    base = cs["h4-lane-b1"]
    for name, c in cs.items():
        assert c.level == base.level == "ladder-c04", name
        assert c.bc == base.bc, name  # 同一起点
        assert c.iters == base.iters == 40, name
        assert c.ppo_schedule == base.ppo_schedule, name
        assert c.target_transitions == base.target_transitions, name
        assert c.seed_rotate == base.seed_rotate, name
        assert c.kickstart_init == base.kickstart_init, name
        # 同种子流是 McNemar 配对前提 ⇒ 三臂必须同值、且是新开的（§15.1 轮换）
        assert c.paired_rotate_seed == base.paired_rotate_seed == 20261001, name
        # 日常段：200 局/点（池只有 860001–860200；判据段另跑 300 局/点）
        assert (c.eval_stages, c.eval_games_per_stage, c.eval_every) == ("2000-2000", 200, 5), name
    # 路径必须三份不同（禁续跑，§15.5）
    assert len({cs[n].traj for n in ARMS}) == 3
    assert len({cs[n].out for n in ARMS}) == 3


def test_head_note_freezes_the_judgement_contract() -> None:
    """判据段 = 尾巴 3 点 it30/35/40 × 300 局/点 @ 池外 seed0 862001 + 同质性前置。"""
    for name in ARMS:
        txt = _text(name)
        assert "862001" in txt, name  # 池外 seed0（与日常段/§67 补评估段不相交）
        assert "it30/it35/it40" in txt, name  # 判据点 = 尾巴三点
        assert "300 局" in txt, name  # 每点局数
        assert "Q ≤ χ²(0.95, df=2) = 5.99" in txt, name  # 尾部不稳 ⇒ 只逐点报告，不得挑单点
        assert "−16%" in txt, name  # 绿线仍冻结为 16%
        assert "paired" in txt, name  # 止损参照物 = 同网格配对差（旧 baseline 已证假阳性）
    # 阈值表（§11）必须逐条写进头注：至少把「不可判/降级」与两条守卫写到
    for name in ARMS:
        txt = _text(name)
        for token in ("−15.4%", "−10.0%", "−3.0%", "−3.9%", "+0.0079", "只报频次"):
            assert token in txt, f"{name}: 缺阈值 {token}"
