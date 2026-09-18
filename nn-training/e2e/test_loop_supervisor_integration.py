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
    events: list[tuple[str, int]] | None = None,
    fail_rounds: set[int] | None = None,
) -> TrainingLoop:
    """造一个**真** TrainingLoop：重活全假，记账全真。"""
    traj = tmp_path / course
    traj.mkdir(parents=True, exist_ok=True)
    loop = TrainingLoop(_args(traj, iters), None, "bun", {})
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

    # ---- 远端 PPO 就绪钩子（R2c-3 的轮询化入口；不存在 ⇒ 跑完即 DONE） ----
    if job_ready is not None:
        _stub(loop, "remote_job_ready", job_ready)
        _stub(loop, "inflight_job_id", lambda it: f"job-{course}-{it}")
    return loop


@pytest.fixture(autouse=True)
def _fake_dist(monkeypatch: pytest.MonkeyPatch) -> None:
    """把与本测试无关的 IO/配置读盘钉成常量（不碰真 rl-config、不起子进程）。"""
    import rl.loop_core as lc

    monkeypatch.setattr(lc.dist_common, "load_dist_config", lambda: {})
    monkeypatch.setattr(lc, "resolve_course_quota", lambda cfg, key, w, s: (w, s, ""))
    monkeypatch.setattr(lc, "_rollout_source", lambda args: "local")
    monkeypatch.setattr(lc, "_run_segment_iters", lambda args: 0)
    monkeypatch.setattr(lc, "spawn_next_collect", lambda *a, **kw: None)
    # 引擎的失败退避（time.sleep(30)）在测试里不真睡——失败语义本身仍然被验证。
    monkeypatch.setattr(lc.time, "sleep", lambda *_a, **_kw: None)


def _ledger_iters(course_dir: Path) -> list[int]:
    import json

    rows = (course_dir / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(r)["iter"] for r in rows if json.loads(r).get("event") == "iteration"]


def _drive(sup: Supervisor, max_steps: int = 200) -> None:
    sup.run_until_idle(max_steps=max_steps)


# ------------------------------------------------ 1) 一个进程服务多门课

def test_two_courses_advance_in_one_process_without_cross_contamination(tmp_path: Path) -> None:
    events: list[tuple[str, int]] = []
    loops = {
        c: _make_loop(tmp_path, c, iters=2, events=events) for c in ("a-course", "b-course")
    }
    runners = {c: LoopRunner(loop=loops[c], course=c, iters=2) for c in loops}
    sup = Supervisor(
        executor=lambda task, q: runners[q.course].run_round(task, q),
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
