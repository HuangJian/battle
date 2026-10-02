"""E1（aim-dodge 批次第一波）起草课契约：三臂**冻结项逐字一致** + 剂量档位正确（2026-10-02）。

为什么值得钉：E1 是三臂配对设计（对照 h4-aim-c0 + 两剂量臂），预注册（κ 剂量、判据段
seed0=864001、主读数 E[d|hit] 与 kill 线）是契约；课程文件被后人静默改（换剂量、换
seed0、换口径）时下面任何一条立刻红。判据全文见 `h4-aim-c0.jsonc` 头注。

2026-10-02 增设止损块冻结：`kickstart_burn`/`paired_kill` 从机器本地 rl-config 迁入课程文件
（DECISIONS §2026-10-02-goalnn-burn-rule-in-course-file）；块缺席时 worker 会静默回落
`baseline`（回测假阳性 49.3% vs paired 0.47%），所以「块在不在、peer 指谁」必须是入库契约。

不钉的：注释文本之外的一切运行期行为（真开课/训练在控制台，不在这份测试的射程内）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biz.course_resolve import load_course
from biz.reward_validation import validate_reward

REPO = Path(__file__).resolve().parents[3]
CURRICULA = REPO / "nn-training" / "curricula"
ARMS = ("h4-aim-c0", "h4-aim-k10", "h4-aim-k25")
# 预注册剂量（DECISIONS §2026-10-01-goalnn-aim-dodge-metrics）：wAim = κ·wHit/E[d|hit]
WAIM = {"h4-aim-c0": None, "h4-aim-k10": 0.0205, "h4-aim-k25": 0.0512}


def _course(name: str):  # type: ignore[no-untyped-def]
    return load_course(CURRICULA / f"{name}.jsonc")


def _text(name: str) -> str:
    return (CURRICULA / f"{name}.jsonc").read_text(encoding="utf-8")


def test_all_three_arms_load_and_pass_reward_validation() -> None:
    for name in ARMS:
        report = validate_reward(_course(name).reward_spec())
        assert not report.errors, f"{name}: {report.errors}"  # errors 是 tuple（空 = 过）


def test_dose_is_frozen_per_arm() -> None:
    for name, waim in WAIM.items():
        spec = _course(name).reward_spec()
        if waim is None:  # 零臂：公式里没有 aim 项，参数表里也不该有 wAim
            assert "wAim" not in spec.params, name
            assert "aimHitDistSum" not in spec.formula, name
        else:
            assert spec.params["wAim"] == pytest.approx(waim), name  # 改剂量 = 换判决条件
            assert "aimHitDistSum" in spec.formula, name
            assert "aimHits" not in spec.formula, name  # 只罚距离积分，不重复计命中数


def test_arms_share_everything_that_must_be_paired() -> None:
    """三臂唯一允许不同的字段 = 剂量 + 路径 + 课名；其余逐字同（配对前提）。"""
    cs = {name: _course(name) for name in ARMS}
    base = cs["h4-aim-c0"]
    for name, c in cs.items():
        assert c.level == base.level == "ladder-c04", name
        assert c.bc == base.bc, name  # 同一起点
        assert c.iters == base.iters == 40, name
        assert c.ppo_schedule == base.ppo_schedule, name
        assert c.target_transitions == base.target_transitions, name
        assert c.seed_rotate == base.seed_rotate, name
        assert c.kickstart_init == base.kickstart_init, name
        # 同种子流是 McNemar 配对前提 ⇒ 三臂必须同值、且是新开的（§15.1 轮换）
        assert c.paired_rotate_seed == base.paired_rotate_seed == 20261002, name
        # 日常段：200 局/点（池 860001–860200；判据段另跑 300 局/点）
        assert (c.eval_stages, c.eval_games_per_stage, c.eval_every) == (
            base.eval_stages,
            base.eval_games_per_stage,
            base.eval_every,
        ), name
    # 路径必须三份不同（禁续跑，§15.5）
    assert len({cs[n].traj for n in ARMS}) == 3
    assert len({cs[n].out for n in ARMS}) == 3


def test_head_note_freezes_the_judgement_contract() -> None:
    """判据段 = 尾巴 3 点 it30/it35/it40 × 300 局/点 @ 池外 seed0 864001（段A）+ 主读数 MDE/kill。"""
    for name in ARMS:
        txt = _text(name)
        assert "864001" in txt, name  # 池外 seed0（与日常段/校准段不相交）
        assert "it30/it35/it40" in txt, name  # 判据点 = 尾巴三点
        assert "300 局" in txt, name  # 每点局数
        assert "E[d|hit]" in txt, name  # 机制主读数
        assert "playerShots/(ticks+1)" in txt, name  # 防刷量读数
        assert "MDE" in txt and "kill 线" in txt, name
        assert "paired" in txt, name  # 止损参照物 = 同网格配对差（旧 baseline 已证假阳性）
    # 每臂头注必须写清剂量口径与身份（k10: κ=0.10 / k25: κ=0.25 / c0: 无 aim 项）
    for token, name in (
        ("κ=0.10", "h4-aim-k10"),
        ("κ=0.25", "h4-aim-k25"),
        ("无 aim 项", "h4-aim-c0"),
    ):
        assert token in _text(name), name


def test_burn_blocks_are_frozen_in_the_course_files() -> None:
    """止损块随腿入库（2026-10-02）：k10/k25 = 显式 paired→c0；对照臂不自杀；头注指「本文件」。

    块缺席 ⇒ worker 静默回落 rl-config 的 `baseline`（机器本地、不入库；历史回测假阳性
    49.3% vs paired 0.47%）。删块/改 peer 必须在 CI 红，而不是等训练停产才发现。
    """
    for name in ("h4-aim-k10", "h4-aim-k25"):
        block = _course(name).kickstart_burn
        assert block is not None, f"{name}: kickstart_burn 块缺失（随腿入库的止损被删？）"
        assert block.mode == "paired", name
        assert block.peer == "h4-aim-c0", name  # 对端不唯一必须显式指定（Wave 2 教训）
        txt = _text(name)
        assert "kickstart_burn" in txt, name
        assert "本文件" in txt, name  # 头注指路 = 课程文件，不再指 rl-config
    c0 = _course("h4-aim-c0")
    assert c0.paired_kill is not None, "c0: paired_kill 块缺失"
    assert c0.paired_kill.enabled is False, "对照臂不自杀（paired_kill.enabled = false）"
    assert "paired_kill" in _text("h4-aim-c0")
