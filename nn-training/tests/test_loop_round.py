"""R2c-3：轮内 13 步表与 `RoundContext`（`rl/loop_round.py` + `rl/loop_round_steps.py`）。

本文件钉两件事：

1. **表是唯一顺序来源**：`STEP_ORDER == ROUND_TASKS`，`STEP_METHOD` 与之一一对应且引擎里
   真有那些方法（加一步而不加实现 ⇒ 这里红）。顺带钉住 `precollect_join` **必须排在
   `prepare_iter` 之前**——这是 R2c-3 对齐真实依赖时纠正的一处真顺序错（预采产出的是本轮
   shard，而 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑 / 清场重建」）。
2. **组合循环只是「按表施加步骤」**：某步给终态即停、`ctx.it` 被整段推进后必须原样带回、
   异常分类与细粒度驱动器共用一份判决（`round_failure`）。

组合循环的测试用**假步骤**（`TrainingLoop.__new__` + 替换 `round_steps`）：这一步验证的是
「驱动器怎么施加表」，不是「步骤体干了什么」——后者由 e2e（真引擎 + 假重活）覆盖。
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from rl.loop_core import TrainingLoop
from rl.loop_round import (
    COLLECT_LOCAL,
    COLLECT_NODE,
    COLLECT_SEGMENT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
    STEP_METHOD,
    STEP_ORDER,
    RoundContext,
    RoundOutcome,
    RoundYieldError,
    advance,
    finish,
    wait_for,
)
from rl.loop_round_steps import RoundSteps
from rl.loop_steps import SmokeVoidRoundError
from rl.loop_tasks import ROUND_TASKS


def _stub(obj: object, name: str, fn: object) -> None:
    """实例级替换方法：字符串名通过 `setattr` ⇒ 不触发 mypy 的 method-assign。

    本文件的**目的**就是「驱动器怎么施加表」，替换步骤序列是手段而非类型缺陷；
    直接赋值会被 mypy 判成 method-assign，而**字面量** `setattr` 又被 ruff B010 拦下
    （e2e 里同一手法，两处保持一致）。
    """
    setattr(obj, name, fn)

# --------------------------------------------------------------- 表的不变式


def test_step_order_is_the_task_table() -> None:
    """13 步的顺序 = `ROUND_TASKS`（单一来源；不可在第二处再抄一遍）。"""
    assert STEP_ORDER == ROUND_TASKS
    assert len(STEP_ORDER) == 13
    assert len(set(STEP_ORDER)) == 13


def test_step_method_covers_every_kind_in_order() -> None:
    """每个 kind 都有引擎实现，且**表的顺序**与步骤顺序一致（字典序即执行序）。"""
    assert list(STEP_METHOD) == list(STEP_ORDER)
    for kind, method in STEP_METHOD.items():
        fn = getattr(TrainingLoop, method, None)
        assert callable(fn), f"{kind} → {method} 在 TrainingLoop 上不存在"
        assert isinstance(fn, types.FunctionType), f"{method} 应当是方法（RoundSteps mixin）"


def test_every_step_method_lives_in_the_round_steps_mixin() -> None:
    """13 步都在 `RoundSteps` 里（组合路径与细粒度驱动器共用同一实现）。"""
    assert {m for m in STEP_METHOD.values()} <= set(dir(RoundSteps))


def test_precollect_join_runs_before_prepare_iter() -> None:
    """★ 依赖方向：预采 shard 先落盘，`prepare_iter` 才能「看盘保留」而不是清场重建。

    顺序反了 ⇒ 上一轮预采**整个作废**（白烧一轮采集），且不会报错——静默退化，故钉住。
    """
    assert ROUND_TASKS[0] == "precollect_join"
    assert ROUND_TASKS.index("precollect_join") < ROUND_TASKS.index("prepare_iter")


# --------------------------------------------------------------- RoundContext


def test_round_context_seg_ran_is_derived_from_collect_mode() -> None:
    ctx = RoundContext(it=3)
    assert ctx.collect_mode == COLLECT_LOCAL and not ctx.seg_ran
    ctx.collect_mode = COLLECT_NODE
    assert not ctx.seg_ran
    ctx.collect_mode = COLLECT_SEGMENT
    assert ctx.seg_ran  # 派生属性：不可能与 collect_mode 分叉


def test_round_context_marks_are_ordered_and_unique() -> None:
    ctx = RoundContext(it=1)
    assert ctx.next_kind() == ROUND_TASKS[0]  # 没走过任何步 ⇒ 第一步
    for kind in ROUND_TASKS[:3]:
        ctx.mark(kind)
    ctx.mark(ROUND_TASKS[0])  # 重复标记不重复记
    assert ctx.done == list(ROUND_TASKS[:3])
    assert ctx.is_done(ROUND_TASKS[1])
    assert ctx.next_kind() == ROUND_TASKS[3]


def test_round_context_next_kind_is_empty_when_all_steps_done() -> None:
    ctx = RoundContext(it=1)
    for kind in ROUND_TASKS:
        ctx.mark(kind)
    assert ctx.next_kind() == ""


# --------------------------------------------------------------- StepResult


def test_step_result_states() -> None:
    assert not advance().is_final and not advance().is_wait
    assert not advance().is_final and not advance().is_wait
    stopped = finish(ROUND_STOP)
    assert stopped.is_final and not stopped.is_wait
    waiting = wait_for("等远端 PPO 回传")
    assert waiting.is_wait and not waiting.is_final
    assert waiting.reason == "等远端 PPO 回传"
    # `finish` 带原因也算终态（原因只作诊断）
    assert finish(ROUND_NEXT, "跑完了").outcome == ROUND_NEXT


# --------------------------------------------------------------- 组合循环


def _bare_loop(tmp_path: Path) -> TrainingLoop:
    """不跑 `__init__` 的引擎骨架：本组用例只验证组合循环怎么施加步骤表。"""
    loop = TrainingLoop.__new__(TrainingLoop)
    loop.args = types.SimpleNamespace(smoke=False)
    loop._consec_fail = 0
    loop._jsonl_path = tmp_path / "training_log.jsonl"
    loop._leg_abort = False
    _stub(loop, "_ledger_apply", lambda _ev: None)
    return loop


def _install_fake_steps(
    loop: TrainingLoop,
    calls: list[str],
    *,
    stop_at: str | None = None,
    advance_it_at: str | None = None,
    raise_at: str | None = None,
    yield_at: str | None = None,
) -> None:
    """把步骤序列换成「记录 + 可选行为」的假步骤（顺序仍按真表）。"""

    def make(kind: str):
        def step(ctx: RoundContext):
            calls.append(kind)
            if kind == raise_at:
                raise RuntimeError("fake step 炸了（模拟引擎异常）")
            if kind == yield_at:
                return wait_for("fake 让位")
            if kind == advance_it_at:
                ctx.it += 5  # 半离线整段会一次推进多轮——返回值必须带回去
            if kind == stop_at:
                return finish(ROUND_STOP)
            return None

        return step

    _stub(loop, "round_steps", lambda: [make(k) for k in STEP_ORDER])


def test_composition_runs_every_step_in_table_order(tmp_path: Path) -> None:
    loop = _bare_loop(tmp_path)
    calls: list[str] = []
    _install_fake_steps(loop, calls)
    out = loop.run_one_round(7)
    assert calls == list(STEP_ORDER)  # 13 步，一步不落，顺序 = 表序
    assert out == RoundOutcome(ROUND_NEXT, 7)


def test_composition_stops_at_the_first_step_with_a_final_outcome(tmp_path: Path) -> None:
    loop = _bare_loop(tmp_path)
    calls: list[str] = []
    _install_fake_steps(loop, calls, stop_at="ppo")
    out = loop.run_one_round(4)
    assert calls[-1] == "ppo"  # 停在 ppo
    assert "export_weights" not in calls and "cleanup" not in calls  # 后面的步不再跑
    assert out == RoundOutcome(ROUND_STOP, 4)


def test_composition_returns_the_it_advanced_by_a_step(tmp_path: Path) -> None:
    """★ 半离线整段会推进 it：丢掉返回值就会重跑整段（比跳轮更贵）。"""
    loop = _bare_loop(tmp_path)
    calls: list[str] = []
    _install_fake_steps(loop, calls, advance_it_at="rollout")
    out = loop.run_one_round(10)
    assert out.it == 15  # 10 + 5，由步骤改写、组合循环原样带回
    assert out.status == ROUND_NEXT


def test_composition_writes_iter_error_and_retries_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rl.loop_round_steps as lrs

    sleeps: list[float] = []
    monkeypatch.setattr(lrs.time, "sleep", lambda s: sleeps.append(s))
    loop = _bare_loop(tmp_path)
    calls: list[str] = []
    _install_fake_steps(loop, calls, raise_at="rollout")
    out = loop.run_one_round(2)

    assert out == RoundOutcome(ROUND_RETRY, 2)  # it 不前跳
    assert loop._consec_fail == 1
    assert sleeps == [30]  # 组合路径（单课程前台）自带退避
    rows = [
        json.loads(line)
        for line in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [r["event"] for r in rows] == ["iter_error"]  # 失败留痕（观测自带牙齿）
    assert rows[0]["iter"] == 2
    assert "RuntimeError" in rows[0]["error"]
    assert calls[-1] == "rollout"  # 异常之后的步不再跑


def test_composition_raises_after_five_consecutive_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rl.loop_round_steps as lrs

    monkeypatch.setattr(lrs.time, "sleep", lambda _s: None)
    loop = _bare_loop(tmp_path)
    loop._consec_fail = 4  # 已是第 5 次
    _install_fake_steps(loop, [], raise_at="rollout")
    with pytest.raises(RuntimeError):
        loop.run_one_round(1)


def test_composition_raises_on_a_dead_leg_instead_of_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9：已判死腿（如远端 401/403）⇒ 直接上抛，不再 5×30s 空转重试。"""
    import rl.loop_round_steps as lrs

    monkeypatch.setattr(lrs.time, "sleep", lambda _s: None)
    loop = _bare_loop(tmp_path)
    loop._leg_abort = True
    _install_fake_steps(loop, [], raise_at="ppo")
    with pytest.raises(RuntimeError):
        loop.run_one_round(1)


def test_composition_refuses_to_yield_loudly(tmp_path: Path) -> None:
    """组合路径没有让位点：步骤自己要求让位 = 设计走岔 ⇒ 响亮报错（不静默停住）。

    ★ 必须是**专门的异常类型**：通用 `except Exception` 会把这个守卫吞进重试阶梯，症状
    变成「每轮都重试同一轮」——写这条用例时实测到过一次（守卫被吃掉），故连「不落
    iter_error 账、不计失败连击」一起钉住。
    """
    loop = _bare_loop(tmp_path)
    _install_fake_steps(loop, [], yield_at="ppo")
    with pytest.raises(RoundYieldError, match="让位"):
        loop.run_one_round(3)
    assert loop._consec_fail == 0
    assert not (tmp_path / "training_log.jsonl").exists()  # 不是失败：不留 iter_error


def test_smoke_void_round_stops_cleanly_without_counting_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rl.loop_round_steps as lrs

    monkeypatch.setattr(lrs.time, "sleep", lambda _s: None)
    loop = _bare_loop(tmp_path)

    def boom(_ctx: RoundContext):
        raise SmokeVoidRoundError("回显")

    _stub(loop, "round_steps", lambda: [boom])
    loop.args = types.SimpleNamespace(smoke=True)
    assert loop.run_one_round(9) == RoundOutcome(ROUND_SMOKE_STOP, 9)
    assert loop._consec_fail == 0  # 冒烟作废不是失败：不计连击、不落 iter_error
    assert not (tmp_path / "training_log.jsonl").exists()

    loop.args = types.SimpleNamespace(smoke=False)
    assert loop.run_one_round(9) == RoundOutcome(ROUND_RETRY, 9)
    assert loop._consec_fail == 0
