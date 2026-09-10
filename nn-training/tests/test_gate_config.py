"""rl/config.py `gates` 块 —— 解析期校验测试（plan/course-exit-and-shutdown.md §3.4）。

纯逻辑（无 torch / 无 bun）。覆盖：好配置加载、§3.4 八条校验逐条响亮报错、
无 gates 老课程逐字节不变、分流声明折叠、budget 可行性 WARNING。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from rl.config import GATE_KINDS, GATE_SPLIT, CourseConfig

BASE: dict[str, Any] = {
    "version": 5,
    "name": "gate-test",
    "gates": {
        "sustain": 3,
        "teacher": {"corpus": "EVAL_SEEDS:860001-860020", "games": 20, "wins": 12},
        "advance_requires": ["G1", "G2", "G3"],
        "rules": [
            {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
            {
                "id": "G2",
                "kind": "skill_floor",
                "max_zero_kill_frac": 0.15,
                "min_kills_rel": 0.7,
                "max_phits_rel": 1.5,
                "verdict": "ADVANCE",
            },
            {
                "id": "G3",
                "kind": "transfer",
                "course": "p10-onset",
                "min_wins": 5,
                "min_kills": 150,
                "verdict": "ADVANCE",
                "enabled": False,
            },
            {
                "id": "G4",
                "kind": "plateau",
                "window_rounds": 10,
                "tol_pp": 8.0,
                "tol_kills": 0.5,
                "metrics": ["win_rate", "kills_mean"],
                "advance_if": ["G1", "G2"],
                "advance_frac": 0.7,
                "verdict": "ADVANCE|REMEDIATE",
            },
            {
                "id": "G5",
                "kind": "budget",
                "advance_if": ["G1", "G2"],
                "advance_frac": 0.7,
                "verdict": "STOP",
            },
            {
                "id": "G9",
                "kind": "hack",
                "window_rounds": 5,
                "pickup_up_rel": 0.5,
                "kills_down_rel": 0.2,
                "verdict": "PAUSE",
            },
        ],
    },
}


def _load() -> CourseConfig:
    return CourseConfig(**copy.deepcopy(BASE))


def _mutate(fn) -> dict[str, Any]:
    d = copy.deepcopy(BASE)
    fn(d)
    return d


# --------------------------------------------------------------- 好配置


def test_gates_valid_config_loads() -> None:
    c = _load()
    assert c.gates is not None
    assert [r.id for r in c.gates.rules] == ["G1", "G2", "G3", "G4", "G5", "G9"]
    assert c.gates.teacher.corpus == "EVAL_SEEDS:860001-860020"
    assert c.gates.sustain == 3


def test_gates_split_declaration_folds_to_single_verdict() -> None:
    """§3.4-1：G4/G5 的 `ADVANCE|REMEDIATE` 是分流声明，verdict 本体单值。"""
    gates = _load().gates
    assert gates is not None
    g4 = gates.rules[3]
    assert g4.is_split is True
    assert g4.base_verdict == "ADVANCE"
    g1 = gates.rules[0]
    assert g1.is_split is False and g1.base_verdict == "ADVANCE"


def test_gates_dormant_cross_course_gate_allowed() -> None:
    """首期休眠（G3/G8/G11）允许 enabled:false，且不计入 advance_requires。"""
    gates = _load().gates
    assert gates is not None
    assert gates.rules[2].enabled is False
    assert "G3" in gates.advance_requires


def test_gates_rel_multiplier_above_one_is_legal() -> None:
    """§3.4-5 实现期修正：`*_rel` 是相对倍数（×教师），1.5 合法（同节示例自证）。"""
    gates = _load().gates
    assert gates is not None
    assert gates.rules[1].max_phits_rel == 1.5


def test_gates_absent_leaves_course_untouched() -> None:
    """缺席 = 关闭（老课程逐字节不变）。"""
    c = CourseConfig(version=5, name="legacy")
    assert c.gates is None
    assert "gates" not in c.flat_overrides()


def test_gate_kinds_catalog_is_fixed() -> None:
    assert {
        "wins_mastery",
        "skill_floor",
        "transfer",
        "plateau",
        "budget",
        "course_valid",
        "retention",
        "hack",
        "teacher_parity",
        "dependency",
        "duty",  # G13 事故熔断（2026-09-11 评审新增）
    } == GATE_KINDS
    assert GATE_SPLIT == "ADVANCE|REMEDIATE"


# ------------------------------------------------- 坏配置：§3.4 八条逐条


@pytest.mark.parametrize(
    ("label", "mut", "needle"),
    [
        (
            "rule id 重复",
            lambda d: d["gates"]["rules"].append({"id": "G1", "kind": "hack", "verdict": "PAUSE"}),
            "id 重复",
        ),
        (
            "未知 kind",
            lambda d: d["gates"]["rules"].append({"id": "GX", "kind": "nope", "verdict": "PAUSE"}),
            "未知 kind",
        ),
        (
            "未知 verdict",
            lambda d: d["gates"]["rules"][0].update({"verdict": "MAYBE"}),
            "未知 verdict",
        ),
        (
            "分流用在错误 kind",
            lambda d: d["gates"]["rules"][5].update({"verdict": GATE_SPLIT}),
            "分流声明",
        ),
        (
            "分数类越界",
            lambda d: d["gates"]["rules"][1].update({"max_zero_kill_frac": 1.5}),
            "越界",
        ),
        (
            "相对倍数为负",
            lambda d: d["gates"]["rules"][0].update({"rel_teacher": -0.1}),
            "不得为负",
        ),
        (
            "window_rounds < 2",
            lambda d: d["gates"]["rules"][3].update({"window_rounds": 1}),
            "window_rounds",
        ),
        ("sustain < 1", lambda d: d["gates"].update({"sustain": 0}), "sustain"),
        ("rules 为空", lambda d: d["gates"].update({"rules": []}), "rules 非空"),
        (
            "可休眠门白名单外的门休眠",
            lambda d: d["gates"]["rules"][5].update({"enabled": False}),
            "可休眠",
        ),
        (
            "plateau 缺 metrics",
            lambda d: d["gates"]["rules"][3].update({"metrics": []}),
            "必须给 metrics",
        ),
        (
            "plateau 未知名指标",
            lambda d: d["gates"]["rules"][3].update({"metrics": ["bogus"]}),
            "未知键",
        ),
        (
            "plateau 缺 advance_frac",
            lambda d: d["gates"]["rules"][3].pop("advance_frac"),
            "advance_frac",
        ),
        (
            "budget 缺 advance_frac",
            lambda d: d["gates"]["rules"][4].pop("advance_frac"),
            "advance_frac",
        ),
        (
            "advance_requires 悬空",
            lambda d: d["gates"]["advance_requires"].append("GZ"),
            "不存在的门",
        ),
        (
            "advance_requires 引非 ADVANCE 门",
            lambda d: d["gates"].update({"advance_requires": ["G9"]}),
            "不是 ADVANCE",
        ),
        (
            "advance_if 悬空",
            lambda d: d["gates"]["rules"][3].update({"advance_if": ["GZzz"]}),
            "未定义的门",
        ),
        (
            "teacher.corpus 空",
            lambda d: d["gates"]["teacher"].update({"corpus": "   "}),
            "corpus 必填非空",
        ),
        ("teacher.games <= 0", lambda d: d["gates"]["teacher"].update({"games": 0}), "必须 >0"),
        (
            "teacher.wins > games",
            lambda d: d["gates"]["teacher"].update({"wins": 99}),
            "[0, games]",
        ),
        (
            "跨课门课程找不到",
            lambda d: d["gates"]["rules"].append(
                {
                    "id": "GT",
                    "kind": "transfer",
                    "course": "no-such-course-xyz",
                    "verdict": "ADVANCE",
                    "enabled": False,
                }
            ),
            "找不到",
        ),
        (
            "跨课门缺 course",
            lambda d: d["gates"]["rules"].append(
                {"id": "GR", "kind": "retention", "verdict": "PAUSE", "enabled": False}
            ),
            "必须给 course",
        ),
        (
            "未知键（拼错）",
            lambda d: d["gates"]["rules"][0].update({"rel_teachr": 0.6}),
            "Extra inputs",
        ),
    ],
)
def test_gates_bad_config_raises(label, mut, needle) -> None:
    with pytest.raises(ValidationError) as ei:
        CourseConfig(**_mutate(mut))
    assert needle in str(ei.value), f"{label}: 期望错误信息含 {needle!r}"


def test_gates_budget_warning_when_budget_too_tight() -> None:
    """§3.4-8（warn-only）：sustain×2 轮 × eval_every 的花销容不下即 WARNING。"""
    d = _mutate(lambda x: x["gates"].update({"est_iter_min": 10.0}))
    g = CourseConfig(**d).gates
    assert g is not None
    warn = g.budget_warnings(max_hours=0.1, eval_every=5)
    assert warn and "预算可行性" in warn[0]
    assert g.budget_warnings(max_hours=24.0, eval_every=5) == []
    assert g.budget_warnings(max_hours=0.0, eval_every=5) == []  # 无预算上限则不判


def test_real_courses_still_load() -> None:
    """存量课程（无 gates 块）解析不受影响。"""
    from rl.config import load_course

    for name in ("c5-margin", "c6-margin", "p4-onset"):
        c = load_course(name)
        assert c.gates is None, name


def test_c6b_course_gates_block_is_machine_readable() -> None:
    """c6b-margin 是首个带 `gates` 块的在营课程：阈值进文件 = 进 course_fp。

    锁三件事：① 七门齐全且 id/kind 与头注释一致；② 教师块的语料必填且
    kills/phits 未测 = 0（依赖它们的相对子项按 §3.4-7 跳过，不许编造）；
    ③ 预算可行（300 局/轮 + eval_every 3 容得下 sustain×2 轮）。
    """
    from rl.config import load_course

    c = load_course("c6b-margin")
    assert c.gates is not None
    kinds = {r.id: r.kind for r in c.gates.rules}
    assert kinds == {
        "G1": "wins_mastery",
        "G2": "skill_floor",
        "G4": "plateau",
        "G5": "budget",
        "G7": "course_valid",
        "G8": "retention",
        "G9": "hack",
        "G13": "duty",
    }
    assert c.gates.teacher.corpus
    assert c.gates.teacher.games == 100 and c.gates.teacher.wins == 50
    assert c.gates.teacher.kills == 0.0 and c.gates.teacher.phits == 0.0
    assert c.gates.advance_requires == ["G1", "G2"]
    # §12.4 effect size：G1 相对起点 ≥+5pp 且同向；baseline 不可缺
    g1 = c.gates.rules[0]
    assert g1.min_gain_pp == 5.0 and g1.require_rising is True
    assert c.gates.baseline_win_rate == 0.26
    assert c.gates.min_train_hours == 2.0
    # 休眠门（G8）不计入 ADVANCE 放行
    assert [r.id for r in c.gates.rules if not r.enabled] == ["G8"]
    # 预算可行：6 轮 × 3 iter × 58 min ≈ 17.4h < 24h → 无 WARNING
    assert c.gates.budget_warnings(max_hours=c.max_hours, eval_every=c.eval_every) == []
