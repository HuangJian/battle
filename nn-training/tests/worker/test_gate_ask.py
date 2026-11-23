"""`GateAsk`：`worker/gate_check.evaluate` 的 9 个参数收成一个对象（`python.refactor.md` §P0-3 批 I）。

判据四条：

1. 入口就是 `evaluate(ask: GateAsk)` —— 没有第二个参数，也没有 `**kwargs` 后门；
2. 字段表是闭集：多一个 `Any` 型的自由位，宽签名迟早从缺省值那条缝里长回来；
3. `frozen` + `kw_only`：位置面不许泄露（`evaluate(rows, course)` 这种互换不抛，只静默 HOLD）；
4. 字段真的被用上：每一条都在**判决行为**上留下可观测的差（`only_kinds` / `override` /
   `course_fp` / `decider` 四个名字尤其——它们互换不会崩，只会让判决说错话）。
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, cast

import pytest

from worker.config import GatesSpec
from worker.gate_check import GateAsk, evaluate

#: 9 个字段的闭集（顺序即 `__init__` 的字段序；`Any` 只给 `course` ——
#: `CourseConfig` 与 `GatesSpec` 两种入参共用它）。
FIELDS = (
    "course",
    "trend_rows",
    "health",
    "budget",
    "now",
    "override",
    "course_fp",
    "decider",
    "only_kinds",
)


#: 教师基线：20 局 12 胜（胜率 0.60）、场均杀 2.95、被击中 0.40。
TEACHER = {
    "corpus": "EVAL_SEEDS:860001-860020",
    "games": 20,
    "wins": 12,
    "kills": 2.95,
    "phits": 0.40,
}


def _gates(**kw: object) -> GatesSpec:
    d: dict = {
        "teacher": dict(TEACHER),
        "rules": [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        "sustain": 1,
    }
    d.update(kw)
    return GatesSpec.model_validate(d)


def _row(win_rate: float = 0.60, seed: str | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {
        "event": "eval_summary",
        "iter": 1,
        "wver": "w001",
        "games": 100,
        "wins": round(win_rate * 100),
        "winRate": win_rate,
        "kills_mean": 3.0,
        "zero_kill_frac": 0.05,
        "phits_mean": 0.4,
        "pickup_mean": 1.0,
        "timeout_frac": 0.0,
    }
    if seed:
        r["seed_fp"] = seed
    return r


# ────────────────────────────── 入口形状


def test_evaluate_takes_exactly_one_ask() -> None:
    params = inspect.signature(evaluate).parameters
    assert list(params) == ["ask"], list(params)
    assert params["ask"].annotation in ("GateAsk", GateAsk)
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_field_table_is_closed() -> None:
    assert tuple(f.name for f in dataclasses.fields(GateAsk)) == FIELDS
    # `Any` 只给 `course`（两种入参共用）；其余字段都有具体注解。
    anys = [f.name for f in dataclasses.fields(GateAsk) if f.type in ("Any", Any)]
    assert anys == ["course"], anys


def test_ask_is_frozen_and_keyword_only() -> None:
    params = inspect.signature(GateAsk).parameters
    kinds = {name: p.kind for name, p in params.items()}
    assert set(kinds.values()) == {inspect.Parameter.KEYWORD_ONLY}, kinds
    ask = GateAsk(course=_gates(), trend_rows=[_row()])
    with pytest.raises(dataclasses.FrozenInstanceError):
        # `object.__setattr__` 会绕过 dataclass 的冻结点，所以走它生成的 `__setattr__`
        ask.__setattr__("decider", "cli")


def test_two_positional_params_are_named_not_ordered() -> None:
    """互换 `course` / `trend_rows` 不能靠"能跑"通过 —— 要么立刻红，要么判决说清楚。"""
    rows = [_row()]
    swapped = evaluate(GateAsk(course=rows, trend_rows=cast(Any, _gates())))
    # 读数行当课程：没有 `.gates` 也没有 `GatesSpec` ⇒ 门关闭（不是崩、不是 ADVANCE）
    assert swapped.verdict == "HOLD"
    assert swapped.readings == (), "读数行当课程 ⇒ 门根本没开（不是崩、也不是 ADVANCE）"
    assert evaluate(GateAsk(course=_gates(), trend_rows=rows)).verdict in {"ADVANCE", "HOLD"}


# ────────────────────────────── 字段各自落位


def test_only_kinds_filters_rules_out_of_play() -> None:
    """`only_kinds` 只求值指定 kind ⇒ 别的门本轮不参与判决（§385 duty 单独查）。"""
    ask_all = GateAsk(course=_gates(), trend_rows=[_row()], now=1.0)
    ask_duty = GateAsk(
        course=_gates(),
        trend_rows=[_row()],
        now=1.0,
        only_kinds=("duty",),
    )
    assert evaluate(ask_all).readings, "全量求值至少一条读数"
    assert evaluate(ask_duty).readings == (), "只查 duty 时指标门必须静默跳过"


def test_override_beats_every_rule() -> None:
    """`override` 是 G12 人工改向，优先级最高（与 `only_kinds` 互换会只剩"看子集"）。"""
    res = evaluate(
        GateAsk(
            course=_gates(),
            trend_rows=[_row()],
            now=1.0,
            override={"verdict": "STOP", "reason": "人手叫停"},
        )
    )
    assert res.verdict == "STOP"
    assert res.override == {"verdict": "STOP", "reason": "人手叫停"}
    assert res.readings, "override 只改判决，不改读数（读数仍逐门在）"


def test_course_fp_filters_rows_of_other_courses() -> None:
    """`course_fp` 是行过滤键：别的课的行不许进判决（互换到 `decider` 就静默错课）。

    旧行缺 `course_fp` 按"全匹配"兼容（§8 口径），所以这里两条行都**带**指纹才分得开。
    """
    mine = _row(win_rate=0.60)
    mine["course_fp"] = "mine"
    theirs = _row(win_rate=0.99)
    theirs["iter"] = 2
    theirs["course_fp"] = "theirs"
    rows = [mine, theirs]

    kept = evaluate(GateAsk(course=_gates(), trend_rows=rows, now=1.0, course_fp="mine"))
    assert kept.verdict == "ADVANCE", "本课的行应当照常进判决"
    filtered = evaluate(GateAsk(course=_gates(), trend_rows=rows, now=1.0, course_fp="deadbeef"))
    assert filtered.verdict == "HOLD", "行全被过滤 ⇒ 数据不足，不许 ADVANCE"


def test_health_and_now_are_passed_through() -> None:
    health = {"gpu_temp": 80}
    res = evaluate(GateAsk(course=_gates(), trend_rows=[_row()], now=1234.5, health=health))
    assert res.now == 1234.5
    assert res.health == health
