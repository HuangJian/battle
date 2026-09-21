"""R2d：单进程多课程 serve 的接线（`rl/loop_serve.py`）——**假引擎 / 假账本**，不碰 torch。

钉的是「驱动者」这一层的性质（plan/r2-loop-task-queue §8）：

1. 一个进程里 N 门课**轮转推进**（不是一门跑完再下一门）⇒ 一门课等外部时别的课照常前进；
2. 引擎**惰性**构建（没在训的课不付 `_setup()` 代价），且跑满时只做 `finish_course`（不停车）；
3. 引擎池**越界驱逐 = 等同一次重启**（重建后 `_setup()` 再走一遍，账本指针不受影响）；
4. 课程级故障（配置缺失/锁被占）隔离：该课进 `skipped`，别的课照跑。

真 rollout/ppo/eval 一律不走（同 `e2e/test_loop_supervisor_integration.py` 的口径）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.loop_core as loop_core
import rl.loop_plan as loop_plan
import rl.loop_serve as loop_serve
import rl.train_ledger as train_ledger
from remote.protocol import COURSE_ENABLE_MARKER
from rl.loop_round import STEP_METHOD
from rl.loop_serve import CourseRuntime, serve
from rl.loop_tasks import ROUND_TASKS


def _enable_course(root: Path, name: str) -> None:
    """代操作员按下「开课」：写账本 + **开课标记**（= `training-enabled.txt`）。

    发现模式的课程表判据是 **账本 ∧ 开课标记**（`loop_plan.enabled_courses`）——
    只写账本的目录在 2026-09-20 之后**不算在训**（用户口径：「课程开训需要用户手动开启」），
    那正是「起了 trainer 就把 tmp/ 下几十门历史课一起拉起来跑」的闸。
    """
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "training_log.jsonl").write_text("", encoding="utf-8")
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")

# --------------------------------------------------------------- 假件


class FakeLedger:
    """账本视图的最小替身（`next_it` + `rows`）——只给 drive 层用。"""

    def __init__(self, next_it: int = 1, rows: list[Any] | None = None) -> None:
        self.next_it = next_it
        self.rows = rows or []


class FakeClock:
    """受控时钟：`sleep` 推进 `now` ⇒ 测试不真睡、也不依赖墙钟顺序。"""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps = 0
        self.on_sleep: Any = None

    def now(self) -> float:
        return self.t

    def sleep(self, sec: float) -> None:
        self.sleeps += 1
        self.t += float(sec)
        if self.on_sleep is not None:
            self.on_sleep(self.sleeps)


class FakeLoop:
    """引擎替身：只记录调度器真的调了它什么（17 个方法里用到的那些）。"""

    instances: list[FakeLoop] = []
    #: 全进程的推进顺序 `(课, it)`——轮转公平性的直接证据（跨课）。
    order: list[tuple[str, int]] = []
    #: `课 → {it: 远端是否已回传}`（缺省 True；测让位时按 it 关掉几轮）。
    ready_map: dict[str, dict[int, bool]] = {}

    def __init__(self, args: Any, backend: Any, bun: str, update_kwargs: Any) -> None:
        self.args = args
        self.backend = backend
        self.bun = bun
        self.setups = 0
        self.finished: list[int] = []
        self.released = 0
        self.executed: list[tuple[int, str]] = []
        self.steps_done: list[str] = []
        FakeLoop.instances.append(self)

    @property
    def course(self) -> str:
        return Path(self.args.traj).name

    # ---- 生命周期钩子（池 / serve 会调）----
    def _setup(self) -> None:
        self.setups += 1

    def _ensure_local_ppo_stack(self) -> None:
        pass

    def release_torch(self) -> None:
        self.released += 1

    def finish_course(self, it: int) -> None:
        self.finished.append(int(it))

    # ---- 执行面 ----
    def remote_job_ready(self, it: int) -> bool:
        """远端 PPO 是否已回传（缺省就绪；测试按 (课, it) 关掉几轮）。"""
        return FakeLoop.ready_map.get(self.course, {}).get(int(it), True)

    def run_one_round(self, it: int) -> Any:
        self.executed.append((int(it), "round"))
        FakeLoop.order.append((self.course, int(it)))
        return loop_core.RoundOutcome(loop_core.ROUND_NEXT, int(it))


def _install_step_methods() -> None:
    """给假引擎补齐 13 步（`STEP_METHOD` 驱动 ⇒ 与真引擎走同一张表，不可能少一步）。"""
    for kind, method in STEP_METHOD.items():

        def make(k: str, m: str) -> Any:
            def step(self: FakeLoop, ctx: Any) -> Any:
                ctx.mark(k)
                self.steps_done.append(k)
                FakeLoop.order.append((self.course, int(ctx.it)))
                return None

            return step

        setattr(FakeLoop, method, make(kind, method))


_install_step_methods()


@pytest.fixture(autouse=True)
def _reset() -> Any:
    FakeLoop.instances.clear()
    FakeLoop.order.clear()
    FakeLoop.ready_map.clear()
    loop_serve.close_course_sinks()
    yield
    FakeLoop.instances.clear()
    FakeLoop.order.clear()
    FakeLoop.ready_map.clear()
    loop_serve.close_course_sinks()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """假课程 + 假账本 + 假引擎：serve 的真接线、零真运算。"""
    monkeypatch.setattr(loop_core, "TrainingLoop", FakeLoop)
    ledger: dict[str, Any] = {"next_it": 1, "rows": []}
    monkeypatch.setattr(loop_plan, "load_ledger", lambda *a, **k: FakeLedger(**ledger))
    monkeypatch.setattr(
        train_ledger, "load_ledger", lambda *a, **k: FakeLedger(next_it=ledger["next_it"])
    )
    opened: list[str] = []

    def fake_open_course(course: str, **kw: Any) -> CourseRuntime:
        opened.append(course)
        args = SimpleNamespace(
            mode="per-tick",
            traj=str(tmp_path / course),
            iters=2,
            out_log="",
            remote_hub_url="",
            remote_token="",
            force=False,
        )
        return CourseRuntime(course=course, args=args)

    monkeypatch.setattr(loop_serve, "open_course", fake_open_course)
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(tmp=tmp_path, ledger=ledger, opened=opened)


# --------------------------------------------------------------- 接线


def test_two_courses_alternate_and_both_finish(env: SimpleNamespace) -> None:
    rep = serve(["a", "b"], prepare=False, bun="bun", iters=2, step_mode=False)

    assert rep.stop_reason == "all_settled"
    assert rep.steps == 4  # 每课 2 轮
    assert env.opened == ["a", "b"]
    for course in ("a", "b"):
        assert rep.courses[course]["rounds_done"] == 2
        assert rep.courses[course]["state"] == "done"
        assert rep.courses[course]["next_it"] == 3
    # ★ 轮转推进（一门课不是一口气跑完两轮）：a,b,a,b —— 公平轮转的直接证据
    assert [(c, it) for c, it in FakeLoop.order] == [("a", 1), ("b", 1), ("a", 2), ("b", 2)]
    for loop in FakeLoop.instances:
        assert [it for it, _ in loop.executed] == [1, 2]
    # 跑满 ⇒ 每课一次 finish_course（**不停车**：进程还要服务别的课）
    assert all(loop.finished == [2] for loop in FakeLoop.instances)
    # 自己建的池在退出时释放全部 torch 栈（注入的池不关，归调用方）
    assert all(loop.released == 1 for loop in FakeLoop.instances)
    assert rep.engines["loaded"] == []


def test_step_mode_runs_the_full_13_step_table(env: SimpleNamespace) -> None:
    rep = serve(["a"], prepare=False, bun="bun", iters=1, step_mode=True)

    assert rep.stop_reason == "all_settled"
    assert rep.steps == len(ROUND_TASKS) == 13
    loop = FakeLoop.instances[0]
    assert loop.steps_done == list(ROUND_TASKS)  # 顺序即依赖顺序
    assert loop.executed == []  # 细粒度路径**不**调轮粒度入口
    assert loop.setups == 1  # 引擎惰性建一次
    assert rep.courses["a"]["rounds_done"] == 1


def test_wait_hands_execution_to_the_other_course(env: SimpleNamespace) -> None:
    """一门课等远端回传时，另一门课的轮照常推进（单进程多课程的核心收益）。"""
    clock = FakeClock()
    FakeLoop.ready_map["a"] = {1: False, 2: False}  # a 的两轮远端结果都还没回

    def flip(_n: int) -> None:
        # 第 2 次空转后，a 的远端结果到了（模拟云端回传）
        if _n >= 2:
            FakeLoop.ready_map["a"] = {1: True, 2: True}

    clock.on_sleep = flip
    rep = serve(
        ["a", "b"],
        prepare=False,
        bun="bun",
        iters=2,
        step_mode=False,
        now=clock.now,
        sleep=clock.sleep,
        max_steps=100,
    )

    assert rep.stop_reason == "all_settled"
    loops = {Path(loop.args.traj).name: loop for loop in FakeLoop.instances}
    # b 全程没被 a 挡住：它的两轮都在 a 让位期间跑完了
    assert [it for it, _ in loops["b"].executed] == [1, 2]
    assert [it for it, _ in loops["a"].executed] == [1, 2]
    assert clock.sleeps >= 2  # 真的发生过「无事可做 → 让位 → 稍后再问」
    assert rep.courses["a"]["reason"] == ""  # 收官后不再残留「在等什么」


def test_pool_eviction_rebuilds_engine_like_a_restart(env: SimpleNamespace) -> None:
    """容量=1 时两门课互相驱逐：**每次重建都再走一遍 `_setup()`**（等同一次重启）。

    这条是引擎池与 serve 的交界：驱逐后 `pool.get` 会产出**新对象**，`ensure_ready` 必须
    认出「对象换了 ⇒ 要走 `_setup()`」，而不是误以为「这门课已经 setup 过」。
    """
    rep = serve(
        ["a", "b"],
        prepare=False,
        bun="bun",
        iters=2,
        step_mode=False,
        cache_courses=1,
        cache_mb=10**9,
    )

    assert rep.stop_reason == "all_settled"
    assert rep.courses["a"]["rounds_done"] == 2 and rep.courses["b"]["rounds_done"] == 2
    # a1 → (被 b 挤掉) → b1 → (被 a 挤掉) → a2 → b2：4 次建栈、至少 2 次驱逐
    assert rep.engines["builds"] == 4
    assert rep.engines["evictions"] >= 2
    assert rep.engines["over_budget"] == 0
    assert len(FakeLoop.instances) == 4
    assert all(loop.setups == 1 for loop in FakeLoop.instances)  # 每个新对象都 setup 过
    # 每个对象各跑一轮（轮粒度：一轮 = 一个任务）
    assert sorted(len(loop.executed) for loop in FakeLoop.instances) == [1, 1, 1, 1]


def test_settled_ledger_never_raises_an_engine(env: SimpleNamespace) -> None:
    """账本说这一轮已结算 ⇒ 队列直接 `done`（不建引擎、不做收官副作用）。"""
    env.ledger["rows"] = [SimpleNamespace(it=1)]

    rep = serve(["a"], prepare=False, bun="bun", iters=0, step_mode=True)

    assert rep.stop_reason == "all_settled"
    assert rep.steps == 0
    assert FakeLoop.instances == []
    assert rep.courses["a"]["state"] == "done"
    assert rep.engines["builds"] == 0


def test_broken_course_is_isolated(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """一门课开不起来（配置缺失/锁被占）⇒ 响亮跳过，其余课照跑。"""
    good = loop_serve.open_course

    def open_course(course: str, **kw: Any) -> CourseRuntime:
        if course == "bad":
            raise SystemExit("[serve] 课程 bad 不存在")
        return good(course, **kw)

    monkeypatch.setattr(loop_serve, "open_course", open_course)
    rep = serve(["bad", "a"], prepare=False, bun="bun", iters=1, step_mode=False)

    assert list(rep.skipped) == ["bad"] and "不存在" in rep.skipped["bad"]
    assert rep.courses["a"]["rounds_done"] == 1
    assert rep.stop_reason == "all_settled"

    rep2 = serve(["bad"], prepare=False, bun="bun", max_steps=5)
    assert rep2.stop_reason == "no_courses" and rep2.steps == 0


# --------------------------------------------------------------- 发现模式（进程不绑课程）


def test_discover_mode_runs_with_zero_courses(env: SimpleNamespace) -> None:
    """**一门课都没有也照常跑**（队列空着等）——空队列不是结束条件（用户 2026-09-18 口径）。"""
    clock = FakeClock()
    rep = serve(
        None,
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        traj_root=str(env.tmp),
        now=clock.now,
        sleep=clock.sleep,
        max_seconds=60.0,
    )

    assert rep.stop_reason == "max_seconds"  # 不是 all_settled：发现模式没有「全收官」这个终点
    assert rep.courses == {} and env.opened == []
    assert clock.sleeps >= 1  # 真的空转了（等新课程）


def test_discover_mode_picks_up_a_course_that_appears_mid_run(env: SimpleNamespace) -> None:
    """扫到新课程账本 ⇒ 自动开课入队（无需重启进程）。"""
    clock = FakeClock()

    def spawn(_n: int) -> None:
        if _n == 2:  # 第 2 次空转时，盘上出现一门新课（控制台此刻按下「开课」）
            _enable_course(env.tmp, "a")

    clock.on_sleep = spawn
    rep = serve(
        None,
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        traj_root=str(env.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_seconds=10.0,
    )

    assert env.opened == ["a"]
    assert rep.courses["a"]["state"] == "done"
    assert rep.courses["a"]["rounds_done"] == 1
    assert rep.stop_reason == "max_seconds"


def test_discover_mode_opens_what_is_already_on_disk(env: SimpleNamespace) -> None:
    """启动时盘上已有两门课 ⇒ 一次全开、不重复开（`_open_courses` 幂等）。"""
    for name in ("a", "b"):
        _enable_course(env.tmp, name)
    clock = FakeClock()

    rep = serve(
        None,
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        traj_root=str(env.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_seconds=5.0,
    )

    assert env.opened == ["a", "b"]  # 各开一次（后续空转不再重复开）
    assert sorted(rep.courses) == ["a", "b"]
    assert all(v["rounds_done"] == 1 for v in rep.courses.values())


def test_a_course_whose_ledger_blows_up_does_not_take_down_the_process(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队阶段读盘失败（账本半写/权限）⇒ 只跳过那门课（故障域 = 单课）。

    没有队列的课会被调度器忽略；若不摘掉它，它会变成一个「看着开着、实际没人跑」的幽灵。
    """
    real_facts = loop_plan.course_facts

    def course_facts(traj: Any, *a: Any, **kw: Any) -> Any:
        if Path(str(traj)).name == "bad":
            raise OSError("账本读不了")
        return real_facts(traj, *a, **kw)

    monkeypatch.setattr(loop_serve, "course_facts", course_facts)
    rep = serve(["bad", "a"], prepare=False, bun="bun", iters=1, step_mode=False)

    assert rep.stop_reason == "all_settled"
    assert "bad" in rep.skipped and "账本读不了" in rep.skipped["bad"]
    assert "bad" not in rep.courses  # 幽灵不留在调度器里
    assert rep.courses["a"]["rounds_done"] == 1


def test_discover_mode_does_not_retry_a_broken_course(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开不起来的课（配置缺失/锁被占）只试一次——否则空转拍会每秒重试、把日志刷爆。"""
    _enable_course(env.tmp, "bad")
    attempts: list[str] = []

    def open_course(course: str, **kw: Any) -> CourseRuntime:
        attempts.append(course)
        raise SystemExit(f"[serve] 课程 {course} 开不起来")

    monkeypatch.setattr(loop_serve, "open_course", open_course)
    clock = FakeClock()
    rep = serve(
        None,
        prepare=False,
        bun="bun",
        step_mode=False,
        traj_root=str(env.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_seconds=5.0,
    )

    assert attempts == ["bad"]  # 一次，不是每次空转一次
    assert list(rep.skipped) == ["bad"]
    assert clock.sleeps >= 2  # 确实空转了好几拍


# --------------------------------------------------------------- 控制面（暂停/恢复）


def test_control_file_pauses_only_the_named_course(
    env: SimpleNamespace, tmp_path: Path
) -> None:
    """控制台写暂停意图 ⇒ 被暂停的课**一步都不推**，别的课照常跑完。"""
    control = tmp_path / "loop-control.json"
    control.write_text(json.dumps({"paused": ["b"]}), encoding="utf-8")
    clock = FakeClock()

    # 显式课程模式 + 全程暂停 b：b 不推进，a 跑完后进程停在「等恢复」（max_seconds 兜底退出）
    rep = serve(
        ["a", "b"],
        prepare=False,
        bun="bun",
        iters=2,
        step_mode=False,
        control_file=str(control),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_seconds=30.0,
    )

    assert rep.stop_reason == "max_seconds"  # 暂停不算收官 ⇒ 不退
    assert rep.courses["a"]["rounds_done"] == 2 and rep.courses["a"]["state"] == "done"
    assert rep.courses["b"]["state"] == "paused"
    assert rep.courses["b"]["rounds_done"] == 0
    assert [c for c, _ in FakeLoop.order] == ["a", "a"]  # b 一步都没跑


def test_control_file_resume_lets_the_course_continue(
    env: SimpleNamespace, tmp_path: Path
) -> None:
    """暂停 → 恢复：队列与账本一个字没动，从原处接着跑（**不重做已完成轮**）。"""
    control = tmp_path / "loop-control.json"
    control.write_text(json.dumps({"paused": ["a"]}), encoding="utf-8")
    clock = FakeClock()

    def unpause(_n: int) -> None:
        if _n >= 2:  # 两拍之后控制台恢复
            control.write_text(json.dumps({"paused": []}), encoding="utf-8")

    clock.on_sleep = unpause
    rep = serve(
        ["a"],
        prepare=False,
        bun="bun",
        iters=2,
        step_mode=False,
        control_file=str(control),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_seconds=60.0,
    )

    assert rep.stop_reason == "all_settled"
    loop = FakeLoop.instances[0]
    assert [it for it, _ in loop.executed] == [1, 2]  # 恢复后接着跑，不是从头
    assert rep.courses["a"]["rounds_done"] == 2


def test_missing_or_broken_control_file_keeps_training(
    env: SimpleNamespace, tmp_path: Path
) -> None:
    """控制面坏掉 ⇒ **保守方向 = 继续训练**（绝不因为读不到意图而误停整条腿）。"""
    control = tmp_path / "loop-control.json"
    control.write_text("{ 这不是 JSON", encoding="utf-8")

    rep = serve(
        ["a"],
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        control_file=str(control),
    )

    assert rep.stop_reason == "all_settled" and rep.courses["a"]["rounds_done"] == 1

    # 文件根本不存在同理（空意图）
    rep2 = serve(
        ["a"],
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        control_file=str(tmp_path / "nope.json"),
    )
    assert rep2.stop_reason == "all_settled"


# --------------------------------------------------------------- CLI 接线


def test_cli_serve_without_courses_means_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--serve` 不给 `--courses` = 发现模式（**不再拒启**）——进程独立于课程。

    这里只钉转发形状（不真跑 supervisor：`serve` 被换成收参数的假件，因为它一旦真跑会
    先做启动前 `git push`）。

    ★ 每个调子都显式带 `--cluster-lock <tmp>`（与同文件那条锁用例同规）：不给就走**默认**
    锁文件 `nn-training/.run_cluster.lock`，而操作员手上真有一台 trainer 在跑是**常态**——
    那时这条只关心 argv 转发形状的用例会因别人的锁 `SystemExit` 而红（2026-09-20 实测：
    pre-commit 门禁被一条与本改动无关的活进程染红）。测试不得依赖「此刻本机没在训练」。
    """
    import run_rl_cluster

    seen: dict[str, Any] = {}

    def fake_serve(courses: Any, **kw: Any) -> Any:
        seen["courses"] = courses
        seen.update(kw)
        return loop_serve.ServeReport(stop_reason="stub")

    monkeypatch.setattr(loop_serve, "serve", fake_serve)
    lock = ["--cluster-lock", str(tmp_path / ".run_cluster.lock")]

    assert run_rl_cluster.main(["--serve", *lock]) == 0
    assert seen["courses"] is None  # 空表 ⇒ None（发现模式），不是空列表
    assert seen["control_file"] is None  # 未显式给 ⇒ 走 loop_control 的默认路径
    assert seen["traj_root"] == "tmp"

    assert run_rl_cluster.main(["--serve", "--courses", "c4-dodge,c5-tick", *lock]) == 0
    assert seen["courses"] == ["c4-dodge", "c5-tick"]

    run_rl_cluster.main(["--serve", "--control-file", "tmp/ctl.json", "--mode", "goal", *lock])
    assert seen["control_file"] == "tmp/ctl.json"
    assert seen["argv"] == ["--mode", "goal"]  # `--mode` 是课程级参数，只透传它

    # ★ §3：`--ppo` 已删除（单一 PPO 路径）——这里只剩 `--mode` 要钉。
    # 否则 argparse 先以 unrecognized arguments 拒启（`--serve --mode goal` 的老坑）。
    run_rl_cluster.main(["--serve", "--mode", "per-tick", *lock])
    assert seen["argv"] == ["--mode", "per-tick"]


def test_cli_serve_takes_a_process_level_single_instance_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """一个进程服务**所有**课程 ⇒ 双开就是两套调度器抢同一批 traj（按课锁拦不住这一类）。

    锁在 `serve()` **之前**把关（真跑起来就晚了），故这里用一个已被自己持有的锁文件表达
    「另一个服务器正在跑」。`--force` 是显式接管（先确认无人在跑）。
    """
    import run_rl_cluster

    lock = tmp_path / ".run_cluster.lock"
    lock.write_text(f"{os.getpid()}|python|0", encoding="utf-8")

    def fake_serve(courses: Any, **kw: Any) -> Any:
        return loop_serve.ServeReport(stop_reason="stub")

    monkeypatch.setattr(loop_serve, "serve", fake_serve)

    with pytest.raises(SystemExit, match="已有单进程服务器在跑"):
        run_rl_cluster.main(["--serve", "--cluster-lock", str(lock)])
    # 拒启时**不**抢锁改内容（别人的锁原样留着）
    assert lock.exists() and lock.read_text(encoding="utf-8").startswith(f"{os.getpid()}|")

    assert run_rl_cluster.main(["--serve", "--cluster-lock", str(lock), "--force"]) == 0
    # 正常收尾后自己释放（`finally`）——不留一个让下次拒启的残锁
    assert not lock.exists()


# --------------------------------------------------------------- 课程参数


def test_course_args_refuses_a_course_flag() -> None:
    """课程由列表给出：附加参数里再传 `--course` 必须响亮拒启（两个来源 = 必然打架）。"""
    with pytest.raises(SystemExit, match="课程由课程列表给出"):
        loop_serve.course_args("c4-dodge", ["--course", "c5-tick"])


def test_course_args_unknown_course_is_loud() -> None:
    with pytest.raises((FileNotFoundError, SystemExit)):
        loop_serve.course_args("no-such-course-xyz")


# 2026-09-20：argv 里必须有 `--echo-config`。原版没传——而 run_rl.main() 的 echo 调用点在
# `if getattr(args, "echo_config", False):` 之后（run_rl.py:212），所以那次 dump **从来没
# 被调用过**：main() 继续往下跑 validate_args → loop 启动 → build_model → 导入 torch
# 并去找 weights/<course>/*.json ⇒ 表现为「本机缺权重 → skip」（本环境）或「有权重但没
# PARITY → pytest.fail」（真机），把一个纯解析链对拍变成了环境/训练链依赖。
# 传 --echo-config 即走**文档化短路**（echo → log → return，在 validate_args 与任何权重/torch
# 之前）⇒ 子进程 ~0.3s、不依赖权重与 torch，任何环境都能真跑。
# 代价：不再覆盖「echo 之后那条链」——那不是本用例的判据（course_args 的解析快照）。
_ORACLE = """
import json, sys
sys.argv = ["run_rl.py", "--course", sys.argv[1], "--echo-config"]
import rl.config as cfg
def fake_echo(args, course, it=1):
    # echo_config 本身不算解析快照（它只是调用方为了让 main() 走到这次 dump 而传的开关）：
    # 不排除会变成「mine 无此键 / theirs 有」的假分叉（实测正是唯一的差异项）。
    print("PARITY:" + json.dumps({k: repr(v) for k, v in vars(args).items()
                                  if not k.startswith("_") and k != "echo_config"},
                                 ensure_ascii=False))
cfg.echo_config = fake_echo
import run_rl
run_rl.main()
"""


def test_course_args_match_run_rl_echo_config(tmp_path: Path) -> None:
    """对拍：`course_args(stem)` ≡ `run_rl.py --course <stem> --echo-config` 的解析快照。

    oracle = 在**子进程里跑 `run_rl.main()` 自己**、把 `echo_config` 换成 dump（同一调用点、
    同一份解析链）——见 `_ORACLE` 上的注释：必须带 `--echo-config` 才会真的走到那次 dump，
    否则会一路跑进训练链（导 torch + 读 weights），本用例就从「解析链对拍」退化成
    「环境能跑训练吗」，实测 5.2s 且本机永远 skip。

    故本用例**不需要**权重/torch（实测 ~0.3s）；拿不到 PARITY 一律算真回归（不再有
    env-blocked 跳过：那条路的唯一成因就是把 torch/权重链误拖进来）。
    """
    stem = "c4-dodge"
    # zh-CN Windows 默认 GBK：oracle stdout 含非 ASCII 时 text=True 会
    # UnicodeDecodeError，subprocess 读线程挂掉 ⇒ proc.stdout 变 None
    # （门禁实测 `AttributeError: 'NoneType' object has no attribute 'splitlines'`）。
    # 显式 UTF-8 + errors=replace，并让子进程也按 UTF-8 吐字。
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", _ORACLE, stem],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    line = next((ln for ln in (proc.stdout or "").splitlines() if ln.startswith("PARITY:")), "")
    if not line:
        tail = (proc.stderr or "").strip()[-300:]
        pytest.fail(f"oracle 没吐出 PARITY（对拍链本身出了问题）：{tail}")
    theirs = json.loads(line[len("PARITY:") :])
    mine = {
        k: repr(v)
        for k, v in vars(loop_serve.course_args(stem)).items()
        if not k.startswith("_") and k != "echo_config"  # 同上：调用开关，不算快照
    }
    assert mine == theirs
