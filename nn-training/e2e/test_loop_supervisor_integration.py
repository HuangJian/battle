"""端到端（假件版）：单进程多课程训练调度跑通 —— **不跑真 rollout/PPO/eval**（R2c-2）。

用户口径（2026-09-18）：写集成测试验证流程，rollout/ppo/eval 一律用假件。
本文件把这三者的**内部运算**全部替换成假件，其余全部是真的：

```
真 TrainingLoop（真 args/真 _setup_common/真 run_one_round 控制流）
  ├─ 真账本写入（_record_iteration → training_log.jsonl）
  ├─ 真门禁/熔断/止损/预算判定（agg=None ⇒ 不熔断；per-tick ⇒ 不止损；无 gates ⇒ 门恒 False）
  └─ 假：_rollout_phase / _serial_ppo / _join_eval / 预采 / 巡检 / 轮转 / 报告打印
真 Supervisor（轮转 + 资源票 + 四态收敛 + 按课隔离）
真 LoopRunner（planner 读账本；RoundOutcome → 四态）
```

钉住的四件事：

1. **一个进程服务多门课**：两个 `TrainingLoop` 在同一进程里交替推进，互不串账；
2. **`WAIT` 让位**：一门课在等远端 PPO 回传时，另一门课照常跑完自己的轮次；
3. **账本是 SSOT**：进程重开后按账本续跑，绝不重复写 `iteration` 行（幂等铁律）；
4. **引擎异常 = 一次失败**：原地重试（it 不前跳、不静默跳轮）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from rl.loop_core import TrainingLoop
from rl.loop_runner import LoopRunner
from rl.loop_scheduler import Supervisor

TS = "2026-09-18 12:00:00"


def _args(traj: Path, iters: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        mode="per-tick",
        # PPO 后端：默认本机（假件跑）；远端课程由 `_make_loop(remote=True)` 改。
        ppo="local",
        traj=str(traj),
        out=str(traj / "weights.json"),
        iters=iters,
        seed=7,
        start_it=None,
        max_hours=0.0,
        eval_every=0,  # 关掉干净评估（eval 全用假件）
        eval_at="",
        rotate_stages=0,
        stages="1,2",
        seeds=1,
        total_stages=2,
        seeds_per_stage=1,
        curriculum_stages="",
        curriculum_start=1,
        curriculum_every=0,
        curriculum_grow=0,
        max_ticks=1000,
        epochs=1,
        mb=2,
        lr=1e-4,
        workers=1,
        local_slots=1,
        keep_iters=1,
        kickstart_ref=False,
        course_obj=None,
        course_name="",
        remote_hub_url="",
        remote_token="",
        remote_transport="",
        export_bundle="",
        run_iters=0,
        collect_only=0,
        # 真止损/门禁判定要读的两个参数（per-tick + at=0 ⇒ 永不止损，行为可预期）
        stop_loss_at=0,
        stop_loss_delta=0.0,
    )


REPORT = {
    "winRate": 0.5,
    "outcomes": {"win": 1, "loss": 1},
    "totalSamples": 100,
    "totalTicks": 1000,
    "scoreStats": {"mean": 1.0, "std": 0.1},
    "dimMeans": {},
}


def _stub(obj: object, name: str, fn: object) -> None:
    """实例级替换方法：字符串名通过 `setattr` ⇒ 不触发 mypy 的 method-assign。

    测试的**目的**就是「重活全假、记账全真」，替换是手段而非类型缺陷：引擎方法签名不变。
    """
    setattr(obj, name, fn)


def _make_loop(
    tmp_path: Path,
    course: str,
    *,
    iters: int = 2,
    job_ready=None,
    remote: bool = False,
    events: list[tuple[str, int]] | None = None,
    fail_rounds: set[int] | None = None,
) -> TrainingLoop:
    """造一个**真** TrainingLoop：重活全假，记账全真。

    `remote=True`：`args.ppo="remote"` ⇒ 本轮 PPO 走三相路径（发布/探一次/落位），
    三个对外动作由 `_install_fake_remote` 替掉。
    """
    traj = tmp_path / course
    traj.mkdir(parents=True, exist_ok=True)
    ns = _args(traj, iters)
    if remote:
        ns.ppo = "remote"
    loop = TrainingLoop(ns, None, "bun", {})
    loop._setup_common()  # 真：写 run_start、定 _traj_root/_total/_jsonl_path/...

    # ---- 假件：rollout / ppo / eval 的内部运算（本测试的题眼） ----
    # 一律用 `setattr(obj, "name", fn)`：字符串名绕过 mypy 的 method-assign（实例级替换
    # 是这个测试的**手段**，不是类型错误——引擎方法签名不变，只是本测试不跑真运算）。
    _stub(loop, "_prepare_iter_dir", lambda it: (traj / f"it{it}").mkdir(parents=True, exist_ok=True))
    for name in (
        "_hot_reload_course",
        "_course_iter",
        "_volume_topup",
        "_evalboard_idle",
        "_dispatch_delayed_eval",
        "_maybe_dispatch_baseline_eval",
        "_export_weights",
        "_write_iter_stats",
        "_rotate_cleanup",
    ):
        _stub(loop, name, lambda *a, **kw: None)
    _stub(loop, "_iteration_pairs", lambda it: [])
    _stub(loop, "_evalboard_yield", lambda: None)
    _stub(loop, "_log_report", lambda it, t0: None)
    _stub(loop, "_join_eval", lambda it: None)

    def fake_rollout(it: int, pairs, dist_cfg, eval_on_round) -> None:
        loop._report = dict(REPORT)  # 让真 _record_iteration 有一份可写的报告

    def fake_ppo(it: int) -> None:
        # 只失败**一次**（第一次尝试）：之后的尝试成功 ⇒ 验证「原地重试后落账且不重复」
        if fail_rounds and it in fail_rounds:
            fail_rounds.discard(it)
            raise RuntimeError("fake ppo 失败（模拟远端连败/引擎异常）")
        loop._ppo_sec = 1.0
        loop._ppo_cloud_sec = 0.5
        loop._total_steps = 10
        loop._chunks_n = 1
        loop._agg = None  # agg=None ⇒ 真 _breaker 短路（不进连击、不告警）

    _stub(loop, "_rollout_phase", fake_rollout)
    _stub(loop, "_serial_ppo", fake_ppo)

    # 真记账 + 事件痕迹（顺序断言用）
    real_record = loop._record_iteration

    def record(it: int) -> None:
        if events is not None:
            events.append((course, it))
        real_record(it)

    _stub(loop, "_record_iteration", record)

    # ---- 远端 PPO 就绪钩子（轮粒度路径的让位入口；不存在 ⇒ 跑完即 DONE） ----
    if job_ready is not None:
        _stub(loop, "remote_job_ready", job_ready)
        _stub(loop, "inflight_job_id", lambda it: f"job-{course}-{it}")
    return loop


def _install_fake_remote(loop: TrainingLoop, course: str, ready: dict[str, bool]) -> None:
    """把远端 PPO 的**三相**换成假件（真驱动、真会话、真让位；不碰网络与 torch）。

    故意**不**替换 `_remote_ppo_step`：让位点就在那里面（R2c-3），整条用例的价值就是
    走真驱动。假掉的只有三个对外动作：发布（回一个会话）、探一次（说不就绪或给结果）、
    落位（把结算字段填成真引擎会填的样子）。
    """
    from rl.loop_round import RemotePpoJob

    def publish(it: int) -> RemotePpoJob:
        return RemotePpoJob(
            it=it,
            jid=f"job-{course}-{it}",
            manifest={"job_id": f"job-{course}-{it}"},
            transport="hub",
            hub_url="http://hub.invalid",
            hub_token="t",
            timeout_sec=60.0,
        )

    def probe(_sess: RemotePpoJob) -> dict | None:
        return {"agg": {"kl": 0.0, "entropy": 1.0, "chunks": 1, "steps": 10}} if ready[
            course
        ] else None

    def land(_sess: RemotePpoJob, result: dict) -> dict:
        # 结算字段：与真 `_remote_ppo_land` 同样的那几项（下游 breaker/止损/账本要用）
        loop._ppo_sec = 1.0
        loop._ppo_cloud_sec = 0.5
        loop._total_steps = 10
        loop._chunks_n = 1
        loop._agg = None  # agg=None ⇒ 真 _breaker 短路（不进连击）
        return result

    _stub(loop, "_remote_ppo_publish", publish)
    _stub(loop, "_remote_ppo_probe", probe)
    _stub(loop, "_remote_ppo_land", land)


@pytest.fixture(autouse=True)
def _fake_dist(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与本测试无关的 IO/配置读盘钉成常量（不碰真 rl-config、不起子进程）。

    ★ 补丁打在哪：轮内那 13 步的实现住在 `rl.loop_round_steps`（mixin），它们**在自己模块的
    全局里**查这些平台函数——只补 `rl.loop_core` 那份名字是打不中的（`dist_common` / `time`
    是模块对象，补在哪个名字空间都算命中，故不在此列）。
    """
    import rl.loop_core as lc
    import rl.loop_round_steps as lrs

    monkeypatch.setattr(lc.dist_common, "load_dist_config", lambda: {})
    for mod in (lc, lrs):
        if hasattr(mod, "resolve_course_quota"):
            monkeypatch.setattr(mod, "resolve_course_quota", lambda cfg, key, w, s: (w, s, ""))
        if hasattr(mod, "_rollout_source"):
            monkeypatch.setattr(mod, "_rollout_source", lambda args: "local")
        if hasattr(mod, "_run_segment_iters"):
            monkeypatch.setattr(mod, "_run_segment_iters", lambda args: 0)
        if hasattr(mod, "spawn_next_collect"):
            monkeypatch.setattr(mod, "spawn_next_collect", lambda *a, **kw: None)
    # 引擎的失败退避（time.sleep(30)）在测试里不真睡——失败语义本身仍然被验证。
    monkeypatch.setattr(lc.time, "sleep", lambda *_a, **_kw: None)


def _ledger_iters(course_dir: Path) -> list[int]:
    import json

    rows = (course_dir / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(r)["iter"] for r in rows if json.loads(r).get("event") == "iteration"]


def _drive(sup: Supervisor, max_steps: int = 200) -> None:
    sup.run_until_idle(max_steps=max_steps)


def _wrap_steps(
    loop: TrainingLoop,
    course: str,
    order: list[tuple[str, str]],
    entered: list[tuple[str, str]] | None = None,
) -> None:
    """给 13 步各套一层记录器（**顺序仍由表决定**）。

    两张痕迹刻意分开记：`entered` = 进过这一步（含让位后又回来的），`order` = **走完**
    的步（让位的那一次不算完）——R2c-3 之后「进入」与「走完」不再是同一件事，用例需要
    分别看见它们（让位不是失败、步骤也没丢，只是这一次没做完）。
    """
    from rl.loop_round import STEP_METHOD, StepResult

    for kind, method in STEP_METHOD.items():
        fn = getattr(loop, method)

        def inner(ctx, _fn=fn, _kind=kind, _course=course):
            if entered is not None:
                entered.append((_course, _kind))
            res = _fn(ctx)
            if not (isinstance(res, StepResult) and res.is_wait):
                order.append((_course, _kind))
            return res

        _stub(loop, method, inner)


def _step_runners(
    loops: dict[str, TrainingLoop], iters: int, clock: dict[str, float], **kw
) -> dict[str, LoopRunner]:
    """细粒度（13 步）runner：planner 用盘上事实出表（facts_fn 缺省 = 判据未知 ⇒ 宁可重做）。"""
    return {
        c: LoopRunner(
            loop=loops[c],
            course=c,
            iters=iters,
            step_mode=True,
            now=lambda: clock["t"],
            **kw,
        )
        for c in loops
    }


def _ledger_shape(course_dir: Path) -> list[tuple[str, int | None]]:
    """账本的事件**形状**（事件名 + it，不看时间戳/runId）：两种驱动必须逐行一致。"""
    import json

    rows = (course_dir / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
    return [(r.get("event"), r.get("iter")) for r in (json.loads(line) for line in rows)]


# ------------------------------------------------ 1) 一个进程服务多门课

def test_two_courses_advance_in_one_process_without_cross_contamination(tmp_path: Path) -> None:
    events: list[tuple[str, int]] = []
    loops = {
        c: _make_loop(tmp_path, c, iters=2, events=events) for c in ("a-course", "b-course")
    }
    runners = {c: LoopRunner(loop=loops[c], course=c, iters=2) for c in loops}
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].executor(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        capacities={"local_ppo": 1, "eval_local": 1, "local_rollout": 2},
    )
    from rl.loop_plan import plan_course

    for c in loops:
        it, _tasks, _facts = plan_course(c, tmp_path / c)
        sup.add_course(c, it)

    _drive(sup)

    # 两门课都跑满 2 轮，各自的账本独立
    assert _ledger_iters(tmp_path / "a-course") == [1, 2]
    assert _ledger_iters(tmp_path / "b-course") == [1, 2]
    # 轮转公平：前两步来自不同课程（不是「跑完 a 再 b」）
    assert len({c for c, _ in events[:2]}) == 2
    # 队列收敛到 done（planner 见 iters 跑满）
    snap = sup.snapshot()
    assert snap["courses"]["a-course"]["state"] == "done"
    assert snap["courses"]["b-course"]["state"] == "done"
    assert runners["a-course"].finish_reason.startswith("iters=2")


# ------------------------------------------------------- 2) WAIT 让位

def test_wait_for_remote_ppo_yields_to_the_other_course(tmp_path: Path) -> None:
    """a 在 it2 等远端 PPO 回传（WAIT），期间 b 必须跑完自己的轮次。"""
    events: list[tuple[str, int]] = []
    clock = {"t": 1000.0}
    ready = {"a2": False}

    def ready_a(it: int) -> bool:
        if it == 2:
            return ready["a2"]
        return True

    a = _make_loop(tmp_path, "a", iters=2, job_ready=ready_a, events=events)
    b = _make_loop(tmp_path, "b", iters=2, events=events)
    runners = {
        "a": LoopRunner(loop=a, course="a", iters=2, now=lambda: clock["t"]),
        "b": LoopRunner(loop=b, course="b", iters=2, now=lambda: clock["t"]),
    }
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].run_round(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        capacities={},
        now=lambda: clock["t"],
    )
    sup.add_course("a", 1)
    sup.add_course("b", 1)

    _drive(sup)
    # a 卡在 it2：账本只有 it1；b 已经跑完 it1+it2（这正是「让位」的证据）
    assert _ledger_iters(tmp_path / "a") == [1]
    assert _ledger_iters(tmp_path / "b") == [1, 2]
    assert sup.courses["a"].state == "waiting"
    assert sup.courses["a"].inflight["a:it2:round"]["jid"] == "job-a-2"  # 在飞集带 job_id

    # 「云机回传」⇒ 只推进时钟（resume_at 到了）+ 置就绪 ⇒ a 继续
    ready["a2"] = True
    clock["t"] += 16.0
    _drive(sup)
    assert _ledger_iters(tmp_path / "a") == [1, 2]
    assert sup.courses["a"].state == "done"
    # 顺序：b 的两轮都发生在 a 的 it2 之前
    assert events.index(("b", 2)) < events.index(("a", 2))


# --------------------------------------------- 3) 账本是 SSOT（重开续跑）

def test_restart_resumes_from_ledger_and_never_rewrites_rows(tmp_path: Path) -> None:
    loops = {"a": _make_loop(tmp_path, "a", iters=2)}
    runners = {"a": LoopRunner(loop=loops["a"], course="a", iters=2)}
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].run_round(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        capacities={},
    )
    sup.add_course("a", 1)
    _drive(sup)
    assert _ledger_iters(tmp_path / "a") == [1, 2]

    # 「进程重开」：另造一个 loop + runner，指针必须由账本给出（=3），且不重写任何行
    loop2 = _make_loop(tmp_path, "a", iters=3)
    runner2 = LoopRunner(loop=loop2, course="a", iters=3)
    sup2 = Supervisor(
        executor=lambda task, q: runner2.run_round(task, q),
        planner=lambda c, it, q: runner2.planner(c, it, q),
        capacities={},
    )
    from rl.loop_plan import plan_course

    it, _, _ = plan_course("a", tmp_path / "a")
    assert it == 3  # ★ 指针从账本重建
    sup2.add_course("a", 1)  # 故意给个旧指针，planner 必须跟上账本
    _drive(sup2)
    assert _ledger_iters(tmp_path / "a") == [1, 2, 3]  # 无重复、无跳轮


# ------------------------------------------- 4) 引擎异常 = 原地重试

def test_engine_exception_retries_same_iteration(tmp_path: Path) -> None:
    loops = {"a": _make_loop(tmp_path, "a", iters=1, fail_rounds={1})}
    runner = LoopRunner(loop=loops["a"], course="a", iters=1)
    sup = Supervisor(
        executor=lambda task, q: runner.run_round(task, q),
        planner=lambda c, it, q: runner.planner(c, it, q),
        capacities={},
    )
    sup.add_course("a", 1)
    traces = sup.run_until_idle(max_steps=10)

    assert any(t.status == "retry" for t in traces)  # 第一次失败 → RETRY
    assert _ledger_iters(tmp_path / "a") == [1]  # it 不前跳，且最终成功落账一次


# ============================= R2c-3：轮内 13 步（细粒度） =============================

def test_step_mode_walks_all_steps_and_rotates_per_step(tmp_path: Path) -> None:
    """细粒度驱动：每课各自走完全部 13 步，且**步级轮转**（让位点密度 = 每步一个）。"""
    from rl.loop_tasks import ROUND_TASKS

    order: list[tuple[str, str]] = []
    clock = {"t": 1000.0}
    loops = {c: _make_loop(tmp_path, c, iters=1) for c in ("a", "b")}
    for c in loops:
        _wrap_steps(loops[c], c, order)
    runners = _step_runners(loops, 1, clock)
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].executor(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        capacities={"local_rollout": 1, "local_ppo": 1, "eval_local": 1},
        now=lambda: clock["t"],
    )
    sup.add_course("a", 1)
    sup.add_course("b", 1)
    _drive(sup, max_steps=200)

    assert _ledger_iters(tmp_path / "a") == [1]
    assert _ledger_iters(tmp_path / "b") == [1]
    assert [k for c, k in order if c == "a"] == list(ROUND_TASKS)
    assert [k for c, k in order if c == "b"] == list(ROUND_TASKS)
    # ★ 步级让位：前两步来自不同课程（轮粒度下这里会是同一课的整轮 13 步连着跑）
    assert len({c for c, _ in order[:2]}) == 2
    assert sup.snapshot()["courses"]["a"]["state"] == "done"


def test_step_mode_yields_at_ppo_and_the_other_course_finishes(tmp_path: Path) -> None:
    """a 在 `ppo` 步等远端回传（三相：已发布、未就绪）⇒ 后面 6 步不跑、执行权交给 b；
    回传后 a 从**同一步**接着走完（不重发布、不重开轮）。

    与旧版的区别（R2c-3）：旧版靠引擎侧 `remote_job_ready` 钩子在**步之前**挡一道；现在
    让位是 `_remote_ppo_step` 在步骤内部产生的，而且它落在「job 已经发布」这个**真状态**
    上——所以用例同时断言「在飞集里有那份 job 的 id」（发布发生过了）。
    """
    from rl.loop_tasks import ROUND_TASKS

    order: list[tuple[str, str]] = []
    entered: list[tuple[str, str]] = []
    clock = {"t": 1000.0}
    ready = {"a": False, "b": True}

    a = _make_loop(tmp_path, "a", iters=1, remote=True)
    b = _make_loop(tmp_path, "b", iters=1)
    for loop, course in ((a, "a"), (b, "b")):
        _wrap_steps(loop, course, order, entered)
        _install_fake_remote(loop, course, ready)
    runners = _step_runners({"a": a, "b": b}, 1, clock)
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].executor(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        # 资源池必须**声明**（`PoolSet` 对未知池响亮报错）：细粒度下采集/PPO/本机 eval
        # 各占一步，容量 1 就是「跨课排队」这句话的机制本身。
        capacities={"local_rollout": 1, "local_ppo": 1, "eval_local": 1},
        now=lambda: clock["t"],
    )
    sup.add_course("a", 1)
    sup.add_course("b", 1)
    _drive(sup, max_steps=200)

    # a 卡在 ppo：**走完**的只有 ppo 之前的 7 步；`ppo` 进来过但让位了（不算走完）；
    # b 走完全部 13 步
    assert [k for c, k in order if c == "a"] == list(ROUND_TASKS[:7])
    assert [k for c, k in entered if c == "a"] == list(ROUND_TASKS[:8])
    assert [k for c, k in order if c == "b"] == list(ROUND_TASKS)
    qa = sup.courses["a"]
    assert qa.state == "waiting" and qa.current is not None and qa.current.kind == "ppo"
    assert [t.kind for t in qa.tasks] == list(ROUND_TASKS[7:])  # 剩下 6 步还在队列里
    assert qa.inflight["a:it1:ppo"]["jid"] == "job-a-1"  # 在飞事实（等谁）可读
    assert qa.tasks[0].attempt == 1  # 让位不是失败：重试计数不动
    assert _ledger_iters(tmp_path / "a") == []  # 未结算：账本不撒谎

    # 「云机回传」⇒ 置就绪 + 推进时钟（resume_at 到）⇒ a 从**同一步**接着走完
    ready["a"] = True
    clock["t"] += 16.0
    _drive(sup, max_steps=200)
    assert _ledger_iters(tmp_path / "a") == [1]
    # 走完的仍是 13 步各一次（让位那次不算完）；但 ppo **进过两次**（第一次让位、
    # 第二次才落位，所以它在 entered 里连着出现两次）——其余步各一次
    assert [k for c, k in order if c == "a"] == list(ROUND_TASKS)
    assert [k for c, k in entered if c == "a"] == [*ROUND_TASKS[:8], "ppo", *ROUND_TASKS[8:]]
    assert sup.snapshot()["courses"]["a"]["state"] == "done"


def test_step_mode_yields_at_precollect_and_the_other_course_finishes(tmp_path: Path) -> None:
    """R2c-3 的「长等待真让位」：a 的 `precollect_join` 在等预采 shard ⇒ 让位，
    b 照常跑完整轮；shard 就绪后 a 从**同一步**接着走完（不重开轮）。

    为什么这一步最值得让位：`join_precollect_child` 就绪前会每 2s 轮询、上限 1 小时
    （双缓冲预采的尾段）。旧形态下那一小时里整个进程都在原地等；单进程多课程下
    这一处就是「一个慢子进程拖垮所有课」的入口。
    """
    from rl.loop_tasks import ROUND_TASKS

    order: list[tuple[str, str]] = []
    clock = {"t": 1000.0}
    ready = {"a": False}

    a = _make_loop(tmp_path, "a", iters=1)
    b = _make_loop(tmp_path, "b", iters=1)
    for loop, course in ((a, "a"), (b, "b")):
        _wrap_steps(loop, course, order)
    # 事实源：引擎钩子说「预采 shard 还没到半波」（真实现是读盘算 completed_pairs）
    _stub(a, "precollect_ready", lambda it: ready["a"])
    runners = _step_runners({"a": a, "b": b}, 1, clock)
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].executor(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        capacities={"local_rollout": 1, "local_ppo": 1, "eval_local": 1},
        now=lambda: clock["t"],
    )
    sup.add_course("a", 1)
    sup.add_course("b", 1)
    _drive(sup, max_steps=200)

    # a 一步都没走（连 `precollect_join` 本身都没进去 —— 让位点**在**步骤之前）
    assert [k for c, k in order if c == "a"] == []
    assert [k for c, k in order if c == "b"] == list(ROUND_TASKS)
    assert _ledger_iters(tmp_path / "a") == []  # 未结算：账本不撒谎
    qa = sup.courses["a"]
    assert qa.state == "waiting"
    assert qa.current is not None and qa.current.kind == "precollect_join"
    assert [t.kind for t in qa.tasks] == list(ROUND_TASKS)  # 整个队列原地不动
    # 读面三件套：状态 + 原因（在等什么）+ 在飞事实
    assert "预采" in qa.reason
    waited = [t for t in sup.traces if t.course == "a" and t.kind == "precollect_join"]
    assert waited and waited[0].status == "wait" and "预采" in waited[0].detail

    # 「shard 到半波」⇒ 置就绪 + 推进时钟 ⇒ a 从同一步接着走完
    ready["a"] = True
    clock["t"] += 16.0
    _drive(sup, max_steps=200)
    assert [k for c, k in order if c == "a"] == list(ROUND_TASKS)
    assert _ledger_iters(tmp_path / "a") == [1]
    assert sup.snapshot()["courses"]["a"]["state"] == "done"
    assert qa.reason == ""  # 推进过就不该再留着「在等什么」


def test_step_mode_writes_the_same_ledger_shape_as_round_mode(tmp_path: Path) -> None:
    """同一门课、同一内容：轮粒度与细粒度驱动的**账本形状逐行一致**（只换驱动）。"""
    clock = {"t": 1000.0}
    loops = {c: _make_loop(tmp_path, c, iters=1) for c in ("round-c", "step-c")}
    round_runner = LoopRunner(loop=loops["round-c"], course="round-c", iters=1)
    step_runner = LoopRunner(
        loop=loops["step-c"], course="step-c", iters=1, step_mode=True, now=lambda: clock["t"]
    )
    runners = {"round-c": round_runner, "step-c": step_runner}
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].executor(task, q),
        planner=lambda c, it, q: runners[c].planner(c, it, q),
        # 细粒度的采集/PPO/本机 eval 各占一步 ⇒ 这些池必须**声明**（轮粒度任务不占池，
        # 故这里声明容量只是为了让两条驱动跑在同一个调度器里）。
        capacities={"local_rollout": 2, "local_ppo": 1, "eval_local": 1},
        now=lambda: clock["t"],
    )
    sup.add_course("round-c", 1)
    sup.add_course("step-c", 1)
    _drive(sup, max_steps=200)

    assert _ledger_shape(tmp_path / "step-c") == _ledger_shape(tmp_path / "round-c")
    assert any(e == "iteration" for e, _ in _ledger_shape(tmp_path / "step-c"))
    # 读面对比：细粒度队列的完成痕迹是**每一步**（13 条），轮粒度只有 1 条
    assert len(sup.courses["step-c"].completed) == 13
    assert len(sup.courses["round-c"].completed) == 1
