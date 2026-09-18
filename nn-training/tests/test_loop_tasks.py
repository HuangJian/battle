"""R2b：任务模型纯逻辑单测（`rl/loop_tasks.py`，plan/r2-loop-task-queue §3）。

三条要钉死的东西（它们决定 R2c 单进程调度器能不能安全重放）：
1. **任务表与顺序**：一轮的 13 步、顺序、以及哪些步骤要占资源池；
2. **幂等判据**：`iteration_recorded` ⇒ 整轮为空（**不得重写账本、不得重发 job**）；
   判据未知的步骤一律不跳（宁可重做，不可误跳——误跳会丢一轮语料）；
3. **失败语义与现主循环逐条一致**（冒烟作废原地重试 / 死腿立刻 ABORT / 5 连击才停）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_tasks import (
    ABORT,
    DONE,
    RESOURCE_OF,
    RETRY,
    ROUND_TASKS,
    WAIT,
    RoundFacts,
    Task,
    TaskResult,
    abort,
    already_done,
    done,
    pending_tasks,
    resolve_failure,
    retry,
    round_tasks,
    waiting,
)

# ------------------------------------------------------------------ 任务表

def test_round_tasks_order_matches_canonical_table() -> None:
    tasks = round_tasks("c4-dodge", 37, params={"mode": "per-tick"})
    assert [t.kind for t in tasks] == list(ROUND_TASKS)
    assert len(tasks) == 13
    assert all(t.it == 37 and t.course == "c4-dodge" for t in tasks)
    # params 自带执行所需的一切（不得回头读进程内跨轮状态）
    assert all(t.params["mode"] == "per-tick" for t in tasks)


def test_task_id_is_the_idempotence_key() -> None:
    t = Task(course="c4", it=9, kind="ppo")
    assert t.task_id == "c4:it9:ppo"
    # 重试只动 attempt，不动身份（幂等键稳定 ⇒ hub 侧去重有效）
    t2 = t.next_attempt()
    assert t2.task_id == t.task_id and t2.attempt == t.attempt + 1


def test_resource_pools_only_on_heavy_steps() -> None:
    assert Task("c", 1, "ppo").resource == "local_ppo"
    assert Task("c", 1, "rollout").resource == "local_rollout"
    assert Task("c", 1, "eval_join").resource == "eval_local"
    # 等待型/记账型步骤不占池——这正是单进程能服务多课程的机制
    for kind in ("prepare_iter", "record_iteration", "gate", "eval_dispatch", "cleanup"):
        assert Task("c", 1, kind).resource is None
    assert set(RESOURCE_OF).issubset(set(ROUND_TASKS))


# ------------------------------------------------------------------ 四态

def test_task_result_four_states() -> None:
    assert done(x=1).status == DONE and done(x=1).payload == {"x": 1}
    w = waiting(1234.5, "等远程 PPO", jid="j1")
    assert (w.status, w.resume_at, w.payload["jid"]) == (WAIT, 1234.5, "j1")
    assert retry("boom").status == RETRY and abort("stop").status == ABORT
    # 出队语义：DONE/ABORT 终态；WAIT/RETRY 归队
    assert done().is_terminal and abort("x").is_terminal
    assert not waiting(None).is_terminal and not retry("x").is_terminal
    with pytest.raises(ValueError):
        TaskResult(status="running")  # 四态之外的拼写必须当场炸


# ------------------------------------------------------------------ 幂等

def test_iteration_recorded_makes_the_whole_round_empty() -> None:
    """★ 幂等铁律：本轮已结算 ⇒ 队列为空（不重写账本、不重发 job）。"""
    facts = RoundFacts(it=37, iteration_recorded=True)
    assert pending_tasks(round_tasks("c", 37), facts) == []


def test_guards_use_only_disk_facts() -> None:
    base = RoundFacts(it=5)
    assert not already_done("rollout", base)  # 什么都没做 ⇒ 全部要做
    # 采集完成判据：要有计划局数（games_planned=0 = 未知计划 ⇒ 不算完成）
    assert already_done("rollout", RoundFacts(it=5, games_settled=10, games_planned=10))
    assert not already_done("rollout", RoundFacts(it=5, games_settled=10, games_planned=0))
    assert not already_done("rollout", RoundFacts(it=5, games_settled=9, games_planned=10))
    assert already_done("volume_topup", RoundFacts(it=5, games_settled=3, games_planned=3))
    assert already_done("export_weights", RoundFacts(it=5, weights_landed=True))
    assert already_done("export_weights", RoundFacts(it=5, weights_archived=True))
    assert already_done("eval_dispatch", RoundFacts(it=5, eval_landed=True))
    assert already_done("precollect_join", RoundFacts(it=5, precollect_consumed=True))
    # 判据未知的步骤一律不跳（宁可重做，不可误跳）
    for kind in ("prepare_iter", "hot_reload", "course_iter", "ppo"):
        assert not already_done(kind, RoundFacts(it=5, iteration_recorded=False))


def test_pending_tasks_keeps_order_and_drops_finished_steps() -> None:
    facts = RoundFacts(it=8, weights_landed=True, eval_landed=True, precollect_consumed=True)
    kinds = [t.kind for t in pending_tasks(round_tasks("c", 8), facts)]
    dropped = ("precollect_join", "export_weights", "eval_dispatch")
    assert kinds == [k for k in ROUND_TASKS if k not in dropped]
    assert kinds == sorted(kinds, key=list(ROUND_TASKS).index)  # 顺序保持
    # 采集未完成 ⇒ rollout/volume_topup 必须留在队列里
    assert "rollout" in kinds and "volume_topup" in kinds


# ------------------------------------------------------------- 失败语义

def test_failure_semantics_match_current_loop() -> None:
    # 冒烟回显：本轮作废、it 原地重试（不计失败连击）
    r = resolve_failure(1, smoke_void=True)
    assert (r.status, r.payload.get("same_iter")) == (RETRY, True)
    # 已判死腿：立刻 ABORT（不再 5×30s 空转——x3-step 事故的教训）
    assert resolve_failure(1, leg_abort=True).status == ABORT
    # 其余：attempt<5 重试、≥5 才停，且 it 不前跳（杜绝静默跳轮丢语料）
    assert resolve_failure(1).status == RETRY
    assert resolve_failure(4).status == RETRY
    assert resolve_failure(5).status == ABORT
