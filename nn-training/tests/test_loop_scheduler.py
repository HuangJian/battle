"""R2c-1：单进程多课程调度器（`rl/loop_scheduler.py` + `rl/loop_plan.py`）。

三条不可交易的性质逐条钉住（plan/r2-loop-task-queue §4）：

1. **同一时刻只跑一个任务**（单线程执行器）——用「执行体重入即计数」证明；
2. **`WAIT` 不占执行权也不占资源票**——一门课等外部时，另一门课照常推进；
3. **故障域按课隔离**——一门课 ABORT（门禁停腿 / 5 连击）只脏它自己的队列。

外加：资源池容量与记账（定案：本机 PPO/eval 跨课排队 = 1）、轮转公平性、
`RoundFacts` 算不出时的保守方向（判据未知 ⇒ 任务不跳）、以及 IO 边缘（`loop_plan`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_plan import (
    course_traj,
    discover_courses,
    inflight_from_journals,
    plan_course,
    settled_shards,
)
from rl.loop_scheduler import (
    ABORTED,
    QUEUE_DONE,
    READY,
    WAITING,
    CourseQueue,
    PoolError,
    PoolSet,
    Supervisor,
)
from rl.loop_tasks import ROUND_TASKS, Task, abort, done, retry, waiting

# ------------------------------------------------------------------ 资源池

def test_pool_capacity_and_accounting() -> None:
    pools = PoolSet({"local_ppo": 1, "eval_local": 1, "local_rollout": 4})
    assert pools.capacity("local_ppo") == 1 and pools.free("local_ppo") == 1
    assert pools.try_acquire("local_ppo") is True
    assert pools.try_acquire("local_ppo") is False  # 容量 1：定案「跨课排队」
    assert pools.held("local_ppo") == 1
    pools.release("local_ppo")
    assert pools.try_acquire("local_ppo") is True
    assert pools.snapshot()["local_rollout"] == {"held": 0, "capacity": 4}


def test_pool_rejects_unknown_name_and_over_release() -> None:
    pools = PoolSet({"local_ppo": 1})
    with pytest.raises(PoolError):
        pools.capacity("nope")  # 配置错必须响亮，不静默降级
    with pytest.raises(PoolError):
        pools.release("local_ppo")  # 释放未持有的票 = 记账已失衡


# ------------------------------------------------------ 单课：顺序 + 单线程

def test_tasks_run_in_order_and_round_advances() -> None:
    """一轮任务按序执行完 ⇒ 指针 +1 并规划下一轮（planner 是唯一来源）。"""
    seen: list[str] = []
    concurrent = 0
    peak = 0

    def exec_(task: Task, q: CourseQueue):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)  # ★ 单线程：任何时刻只可能 1 个任务在跑
        seen.append(task.kind)
        concurrent -= 1
        return done()

    rounds = {1: ["prepare_iter", "rollout"], 2: ["prepare_iter", "rollout"]}

    def planner(course: str, it: int, q: CourseQueue) -> list[Task]:
        return [Task(course, it, k) for k in rounds.get(it, [])]

    sup = Supervisor(exec_, planner, capacities={"local_rollout": 2})
    q = sup.add_course("c4", 1)
    sup.run_until_idle()

    assert seen == ["prepare_iter", "rollout", "prepare_iter", "rollout"]
    assert q.next_it == 3 and q.rounds_done == 2
    assert q.state == QUEUE_DONE  # planner 第 3 轮返回空 ⇒ 没有可做的事
    assert q.completed == ["c4:it1:prepare_iter", "c4:it1:rollout", "c4:it2:prepare_iter", "c4:it2:rollout"]
    assert peak == 1


# ------------------------------------------- 多课：轮转公平 + WAIT 让位

def test_round_robin_fairness_across_courses() -> None:
    """轮转而不是「跑完一门再跑下一门」——否则末位课程永远在饿。"""
    order: list[str] = []

    def exec_(task: Task, q: CourseQueue):
        order.append(task.course)
        return done()

    # 每课只有一轮、每轮 2 个任务 ⇒ 总数有限（不然 planner 会一直供应）
    sup = Supervisor(
        exec_,
        lambda c, it, q: [Task(c, it, "gate") for _ in range(2)] if it == 1 else [],
        capacities={},
    )
    for c in ("a", "b", "c"):
        sup.add_course(c, 1)
    sup.run_until_idle()
    assert order[:3] == ["a", "b", "c"]  # 第一圈每课各一步
    assert order == ["a", "b", "c", "a", "b", "c"]


def test_wait_yields_and_other_course_proceeds() -> None:
    """★ 核心性质：一门课 WAIT 时不占执行权——另一门课照常推进（时钟可注入，测试不真睡）。"""
    clock = {"t": 1000.0}
    calls: list[str] = []
    waited: set[str] = set()

    def exec_(task: Task, q: CourseQueue):
        calls.append(f"{task.course}:{task.kind}")
        # 第一次问该任务 = 还没好（WAIT）；之后 = 结果已到（与真实 job 轮询同形）
        if task.course == "slow" and task.kind == "ppo" and task.task_id not in waited:
            waited.add(task.task_id)
            return waiting(clock["t"] + 60.0, "等远程 PPO", jid="job-1", dispatch="push")
        return done()

    plan = {"slow": ["rollout", "ppo", "record_iteration"], "fast": ["rollout", "ppo", "record_iteration"]}
    sup = Supervisor(
        exec_,
        # 只供应一轮（planner 每轮返回非空 ⇒ 永远不会 idle，会让断言失去意义）
        lambda c, it, q: [Task(c, it, k) for k in plan[c]] if it == 1 else [],
        capacities={"local_rollout": 1, "local_ppo": 1},
        now=lambda: clock["t"],
    )
    sup.add_course("slow", 1)
    slow = sup.courses["slow"]
    sup.add_course("fast", 1)

    sup.run_until_idle(max_steps=50)
    # slow 卡在 ppo（WAIT）时 fast 已经跑完三件事
    assert slow.state == WAITING and slow.resume_at == 1060.0
    assert slow.inflight["slow:it1:ppo"] == {"jid": "job-1", "dispatch": "push"}
    assert sup.courses["fast"].state == QUEUE_DONE
    assert "fast:record_iteration" in calls

    # 时钟推进到 resume_at ⇒ slow 继续（job_id 仍在在飞集里，直到它真正完成）
    clock["t"] = 1061.0
    sup.run_until_idle(max_steps=50)
    assert slow.state == QUEUE_DONE and slow.next_it == 2
    assert slow.inflight == {}  # 完成后在飞集清空


def test_wait_does_not_hold_pool_ticket() -> None:
    """WAIT 必须把票还掉，否则「等云机」的那门课会把本机 PPO 池永久占住。"""
    pools = PoolSet({"local_ppo": 1})

    def exec_(task: Task, q: CourseQueue):
        return waiting(1e12, "永远在等")

    sup = Supervisor(
        exec_,
        lambda c, it, q: [Task(c, it, "ppo")],
        pools=pools,
    )
    sup.add_course("a", 1)
    sup.step()
    assert pools.held("local_ppo") == 0  # ★ 票已归还
    assert pools.free("local_ppo") == 1


def test_pool_gate_blocks_second_course_when_ticket_is_held() -> None:
    """★ 容量 1 池：后台仍在干活的那门课**留着票**，另一门课的同一资源步骤不被执行。

    这就是「跨课排队」的真实形态（用户定案 ③）：本机 eval 的局还在跑（`waiting(hold=True)`），
    另一门课就不能同时开自己的本机 eval——它只能等（让位，不是阻塞执行器）。
    """
    started: list[str] = []
    released = {"a": False}

    def exec_(task: Task, q: CourseQueue):
        started.append(task.course)
        if task.course == "a" and not released["a"]:
            return waiting(1e12, "本机 eval 局还在跑", hold=True)
        return done()

    sup = Supervisor(
        exec_,
        lambda c, it, q: [Task(c, it, "eval_join")] if it == 1 else [],
        capacities={"eval_local": 1},
    )
    sup.add_course("a", 1)
    a = sup.courses["a"]
    sup.add_course("b", 1)
    traces = sup.run_until_idle(max_steps=10)

    assert started == ["a"]  # b 的 eval_join 从没被执行（被池挡住）
    assert [t.action for t in traces] == ["ran", "blocked_pool"]
    assert traces[1].course == "b" and "eval_local" in traces[1].detail
    assert a.holds == {"a:it1:eval_join": "eval_local"}  # 票仍被 a 持有
    # a 的后台干完（完成该任务）⇒ 放票 ⇒ b 能进来
    released["a"] = True
    a.resume_at = 0.0
    sup.run_until_idle(max_steps=10)
    assert started == ["a", "a", "b"]
    assert a.holds == {}


def test_pool_gate_is_per_pool_not_global() -> None:
    """不同池互不相扰：a 占着 eval_local，不应挡住 b 的本机 PPO（它们争的不是同一样东西）。"""
    started: list[str] = []

    def exec_(task: Task, q: CourseQueue):
        started.append(f"{task.course}:{task.kind}")
        if task.kind == "eval_join":
            return waiting(1e12, "本机 eval 还在跑", hold=True)
        return done()

    def planner(c: str, it: int, q: CourseQueue) -> list[Task]:
        if it != 1:
            return []
        return [Task(c, it, "eval_join")] if c == "a" else [Task(c, it, "ppo")]

    sup = Supervisor(exec_, planner, capacities={"eval_local": 1, "local_ppo": 1})
    sup.add_course("a", 1)
    sup.add_course("b", 1)
    sup.run_until_idle(max_steps=10)
    assert "b:ppo" in started  # 本机 PPO 没被 eval_local 的占用影响


# --------------------------------------------------- 重试 / 停腿 / 故障域

def test_retry_increments_attempt_then_aborts_after_max() -> None:
    """5 连击才停（与主循环同语义），退避期间本课让位不空转。"""
    clock = {"t": 0.0}
    attempts: list[int] = []

    def exec_(task: Task, q: CourseQueue):
        attempts.append(task.attempt)
        return retry("boom")

    sup = Supervisor(
        exec_,
        lambda c, it, q: [Task(c, it, "rollout")],
        capacities={"local_rollout": 1},
        now=lambda: clock["t"],
    )
    q = sup.add_course("a", 1)
    for _ in range(5):
        sup.step()
        clock["t"] += 31.0  # 越过退避窗口
    assert attempts == [1, 2, 3, 4, 5]
    assert q.state == ABORTED and "连续失败" in q.reason


def test_abort_isolates_to_one_course() -> None:
    """★ 故障域：一门课停腿，另一门课照常跑完（与停机达令按课程同口径）。"""
    def exec_(task: Task, q: CourseQueue):
        if task.course == "bad":
            return abort("门禁 ABORT")
        return done()

    sup = Supervisor(
        exec_,
        lambda c, it, q: [Task(c, it, k) for k in ("rollout", "record_iteration")] if it == 1 else [],
        capacities={"local_rollout": 1},
    )
    sup.add_course("bad", 1)
    sup.add_course("good", 1)
    sup.run_until_idle()

    assert sup.courses["bad"].state == ABORTED
    assert sup.courses["good"].state == QUEUE_DONE
    snap = sup.snapshot()
    assert snap["courses"]["bad"]["reason"] == "门禁 ABORT"
    assert snap["courses"]["good"]["rounds_done"] == 1


def test_executor_exception_is_a_retry_not_a_crash() -> None:
    """执行体抛异常 = 一次失败（与主循环 except 分支同语义），不得打崩整个调度器。"""
    def exec_(task: Task, q: CourseQueue):
        raise RuntimeError("transient")

    sup = Supervisor(exec_, lambda c, it, q: [Task(c, it, "ppo")], capacities={"local_ppo": 1})
    q = sup.add_course("a", 1)
    sup.step()
    assert q.state == WAITING and q.current is not None and q.current.attempt == 2
    assert q.reason == ""  # 还没到停腿


def test_run_until_idle_guards_against_spinning_planner() -> None:
    """planner 空转（每轮返回非空却不推进）⇒ 响亮抛错，而不是无限循环。"""
    sup = Supervisor(
        lambda task, q: done(),
        lambda c, it, q: [Task(c, it, "gate")],  # 永远返回任务
        capacities={},
    )
    sup.add_course("a", 1)
    with pytest.raises(RuntimeError):
        sup.run_until_idle(max_steps=5)


def test_pause_and_resume() -> None:
    sup = Supervisor(lambda task, q: done(), lambda c, it, q: [], capacities={})
    q = sup.add_course("a", 1, [Task("a", 1, "gate")])
    sup.pause("a", "操作员暂停")
    assert sup.step() is None and q.state == "paused"
    sup.resume("a")
    assert q.state == READY
    sup.step()
    assert q.state == QUEUE_DONE


# ------------------------------------------------------- IO 边缘（loop_plan）

def _ledger(traj: Path, *, last_iter: int = 1, with_iteration: bool = True) -> None:
    traj.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [
        {"event": "run_start", "time": "2026-09-18 12:00:00", "rotateSeed": 7}
    ]
    if with_iteration:
        rows.append(
            {
                "event": "iteration",
                "iter": last_iter,
                "time": "2026-09-18 12:01:00",
                "samples": 100,
                "epochs": 1,
                "kl": 0.01,
                "entropy": 0.9,
                "winRate": 0.2,
                "ppo_sec": 10.0,
            }
        )
    (traj / "training_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def test_settled_shards_counts_manifests_only(tmp_path: Path) -> None:
    d = tmp_path / "it2"
    (d / "rl_s1_seed2").mkdir(parents=True)
    (d / "rl_s1_seed2" / "manifest.json").write_text("{}", encoding="utf-8")
    (d / "rl_s1_seed3").mkdir(parents=True)  # 未结算（没有 manifest）
    assert settled_shards(tmp_path, 2) == 1
    assert settled_shards(tmp_path, 9) == 0


def test_plan_course_uses_ledger_pointer_and_stays_conservative(tmp_path: Path) -> None:
    traj = tmp_path / "c4"
    _ledger(traj, last_iter=1)
    it, tasks, facts = plan_course("c4", traj)
    assert it == 2 and facts["iterations"] == 1
    assert facts["iteration_recorded"] is False  # it2 还没结算
    kinds = [t.kind for t in tasks]
    # 采集计划未知（games_planned=0）⇒ rollout 不跳（保守：宁可重做，不可误跳）
    assert kinds == list(ROUND_TASKS) and "rollout" in kinds


def test_plan_course_marks_settled_round_as_empty(tmp_path: Path) -> None:
    """账本里已有该 it 的 iteration 行 ⇒ 整轮为空（★ 幂等铁律：不重写账本、不重发 job）。"""
    traj = tmp_path / "c4"
    _ledger(traj, last_iter=3)  # next_it 由 last_iter 决定
    it, tasks, _ = plan_course("c4", traj)
    # last_iter=3 ⇒ next_it=4，而账本里没有 it4 ⇒ 仍应给出完整任务表
    assert it == 4 and tasks
    # 人为把账本改成「it1 已结算且 next_it=1」不可能（next_it 单调），故直接测判据层：
    from rl.loop_tasks import RoundFacts, pending_tasks, round_tasks

    assert pending_tasks(round_tasks("c4", 1), RoundFacts(it=1, iteration_recorded=True)) == []


def test_inflight_from_journals_reads_job_id(tmp_path: Path) -> None:
    traj = tmp_path / "c5"
    _ledger(traj, last_iter=0, with_iteration=False)
    d = traj / "it1"
    d.mkdir(parents=True)
    jp = d / "commit_journal.jsonl"
    jp.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"event": "commit_journal", "op": "start", "phase": "ppo_remote", "round": "1"},
                {
                    "event": "commit_journal",
                    "op": "attach",
                    "phase": "ppo_remote",
                    "round": "1",
                    "jid": "job-42",
                    "dispatch": "push",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    got = inflight_from_journals(traj)
    assert len(got) == 1 and got[0]["jid"] == "job-42" and got[0]["dir"] == "it1"
    assert inflight_from_journals(traj, it=9) == []


def test_discover_courses_by_ledger_existence(tmp_path: Path) -> None:
    _ledger(tmp_path / "c4", with_iteration=False)
    (tmp_path / "not-a-course").mkdir()
    assert discover_courses(tmp_path) == ["c4"]
    assert discover_courses(tmp_path / "nope") == []
    assert course_traj(tmp_path, "c4") == tmp_path / "c4"
