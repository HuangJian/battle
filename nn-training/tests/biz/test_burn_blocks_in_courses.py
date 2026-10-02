"""E2/E3a 四臂 + C 腿两门的止损块冻结（2026-10-02 第二刀 P1；plan/burn-rule-in-course-file §9）。

为什么值得钉：这六门课的止损规则原先住在 `nn-training/rl-config.json`（机器本地、不进 git）
的 `courses.<课>.{kickstart_burn,paired_kill}`——换机器 / 新克隆读不到 ⇒ 静默回落 `baseline`
（回测假阳性 49.3% vs paired 0.47%）。2026-10-02 起随腿入库（DECISIONS
§2026-10-02-goalnn-burn-rule-in-course-file）；第二刀删掉 rl-config 读面后，这些块是止损的
**唯一**来源，删块/改 peer 必须是 CI 红，而不是等换机器训练停产才发现。

- `h4-hurt-{f75,f150}`：`kickstart_burn = {mode: paired, peer: h4-hurt-c0}`（E2 处理臂）
- `h4-encl-{f75,f150}`：`kickstart_burn = {mode: paired, peer: h4-encl-c0}`（E3a 处理臂）
- `x20-clutch`：`paired_kill.enabled = true`（C-w 中点杀臂 opt-in）
- `x20-clutch-null`：`paired_kill.self_kill = false`（C-0 事故后：对照臂永不自杀）

不钉的：阈值数值（走 worker 模块常量 5pp/3 点，N1）、运行期行为（真开课在控制台）。
"""

from __future__ import annotations

from pathlib import Path

from biz.course_resolve import load_course

REPO = Path(__file__).resolve().parents[3]
CURRICULA = REPO / "nn-training" / "curricula"

#: 处理臂 → 显式对端（对端不唯一必须点名；Wave 2 实测教训）
BURN_ARMS = {
    "h4-hurt-f75": "h4-hurt-c0",
    "h4-hurt-f150": "h4-hurt-c0",
    "h4-encl-f75": "h4-encl-c0",
    "h4-encl-f150": "h4-encl-c0",
}


def _course(name: str):  # type: ignore[no-untyped-def]
    return load_course(CURRICULA / f"{name}.jsonc")


def _text(name: str) -> str:
    return (CURRICULA / f"{name}.jsonc").read_text(encoding="utf-8")


def test_treatment_arms_freeze_the_paired_burn_blocks() -> None:
    """四臂的 `kickstart_burn` 块 = 显式 paired→c0；阈值不另写（模块常量）。"""
    for name, peer in BURN_ARMS.items():
        block = _course(name).kickstart_burn
        assert block is not None, f"{name}: kickstart_burn 块缺失（随腿入库的止损被删？）"
        assert block.mode == "paired", name
        assert block.peer == peer, name  # 对端不唯一必须显式指定（Wave 2 教训）
        assert block.margin_pp is None and block.points is None, (
            f"{name}: 阈值走模块常量（5pp/3 点）——改数值是新的实验裁决（N1）"
        )
        txt = _text(name)
        assert "kickstart_burn" in txt, name
        # 头注指路 = 本文件（不再指机器本地、不入库的 rl-config）
        assert "本文件" in txt, name
        assert "rl-config `courses." not in txt, name


def test_control_arms_point_at_the_course_blocks_not_rl_config() -> None:
    """两个对照臂的头注指 f75/f150 各自的课程文件块（而不是 rl-config 的路径）。"""
    for name, token in (("h4-hurt-c0", "h4-hurt"), ("h4-encl-c0", "h4-encl")):
        txt = _text(name)
        assert "kickstart_burn" in txt, name
        assert f"`courses.{token}-f75" not in txt, name
        assert "rl-config" not in txt, f"{name}: 止损指路不得再指 rl-config"
        assert "随腿入库" in txt, name


def test_clutch_pair_freezes_the_midpoint_kill_blocks() -> None:
    """C-w 显式 opt-in；C-0 永不自杀（2026-09-25 事故的规则 Trustee）。"""
    w = _course("x20-clutch")
    assert w.paired_kill is not None, "x20-clutch: paired_kill 块缺失"
    assert w.paired_kill.enabled is True, "C-w 中点杀臂必须显式 opt-in（§2026-09-26-paired-kill-opt-in）"
    n = _course("x20-clutch-null")
    assert n.paired_kill is not None, "x20-clutch-null: paired_kill 块缺失"
    assert n.paired_kill.self_kill is False, (
        "C-0 事故后：对照臂永不自杀（§2026-09-25-clutch-null-kill）"
    )
    for name in ("x20-clutch", "x20-clutch-null"):
        txt = _text(name)
        assert "paired_kill" in txt, name
        assert "随腿入库" in txt, name  # 止损节的指路 = 本文件的块
