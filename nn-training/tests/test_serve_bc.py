"""R3-4：`serve` 带 BC 课 —— 单进程 supervisor × BC 引擎（`rl/loop_serve.py` × `rl/bc_loop.py`）。

**这是「让 serve 也能带 BC 课」的验收用例**（用户口径：一个 trainer 进程服务所有课程，
BC 课不再需要一个专属进程）。只把最重的活换成假件——真组件全在环上：

| 真 | 假 |
|---|---|
| `open_course` 的 BC 分支（`_open_bc_course`）、单实例锁、`BcRuntime` 解析 | 语料采集（`collect_corpus`） |
| `BcLoop` 引擎（一轮切段/让位/落位/账本）、`LoopRunner`、`Supervisor`、`EnginePool` | job 发布/落位/归档 |
| `bc_round_completed` 账本、`find_round_job`（盘上认领） | 云端 hub（`_request` 返回 404→200） |

钉住三条性质：

1. **BC 课与 RL 课在同一个进程里互相让位**：BC 在等云端 job 回传时，另一个进程的课程照常推进
   （这正是「一个个 trainer 进程」换掉的墙钟）；
2. **重入/驱逐都不重发布**——BC 的续训按 jid 存，重发 = 新 jid = 从头训；引擎被池驱逐后
   重建的那一门课必须从盘上认领已有的 job（`find_round_job`）；
3. **粒度按课程种类**：BC 恒为「一轮 = 一个任务」（13 步表是 RL 的一轮，硬套会 ABORT）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.bc_config as bc_config
import rl.bc_loop as bc_loop
import rl.loop_core as loop_core
import rl.loop_plan as loop_plan
import rl.loop_serve as loop_serve
import rl.train_ledger as train_ledger
from remote import hub_client
from rl.bc_ledger import ROUND_DONE_EVENT, read_events
from rl.loop_serve import CourseRuntime, serve
from rl.loop_tasks import ROUND_TASKS

#: BC 课的附加参数（serve 级 argv）：强制 pull + 本机 hub（否则会真去打 rl-config 里的地址）
BC_ARGV = [
    "--remote",
    "--remote-hub-url",
    "http://hub",
    "--remote-token",
    "tok",
    "--remote-transport",
    "pull",
]


class FakeClock:
    """受控时钟：`sleep` 推进 `now` ⇒ 不真睡、也不依赖墙钟。"""

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


class FakeHub:
    """假 hub：`/result` 前 `ready_after-1` 次 404（排队中），之后给结果。"""

    def __init__(self, *, ready_after: int = 2) -> None:
        self.polls = 0
        self.ready_after = ready_after

    def request(self, base: str, token: str, path: str, timeout: float = 0.0) -> Any:
        if path.endswith("/result"):
            self.polls += 1
            if self.polls < self.ready_after:
                return 404, b"{}"
            return 200, json.dumps({"metrics": {"epochs": 1}}).encode("utf-8")
        if path.endswith("/bc-metrics"):
            return 200, b'{"rows": []}'
        return 404, b"{}"


class FakeRLLoop:
    """RL 侧的引擎替身（只为「让位时别的课照常推进」提供证据——不跑真 rollout/PPO）。"""

    order: list[tuple[str, int]] = []

    def __init__(self, args: Any, backend: Any, bun: str, update_kwargs: Any) -> None:
        self.args = args

    @property
    def course(self) -> str:
        return Path(self.args.traj).name

    def _setup(self) -> None: ...
    def _ensure_local_ppo_stack(self) -> None: ...
    def release_torch(self) -> None: ...
    def finish_course(self, it: int) -> None: ...

    def remote_job_ready(self, it: int) -> bool:
        return True

    def run_one_round(self, it: int) -> Any:
        FakeRLLoop.order.append((self.course, int(it)))
        return loop_core.RoundOutcome(loop_core.ROUND_NEXT, int(it))


@pytest.fixture(autouse=True)
def _reset() -> Any:
    FakeRLLoop.order.clear()
    loop_serve.close_course_sinks()
    yield
    FakeRLLoop.order.clear()
    loop_serve.close_course_sinks()


@pytest.fixture
def bc_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """在**临时 curricula 目录**里造一门真 BC 课程（真配置解析，不碰仓根课程与 tmp/）。

    直接改写仓根真实课程的 `traj/out/data_dir/backup_dir` 会把测试写进真账本/真归档——
    所以整份课程文件进临时 curricula，并把 `rl.bc_config.CURRICULA_DIR` 指过去（
    `is_bc_course` / `resolve_bc_runtime` 都读这个模块常量 ⇒ 一处即可）。
    """
    from rl.jsonc import load as load_jsonc

    name = "bc-int"
    d = load_jsonc(str(ROOT / "curricula" / "bc-c4-v3.bc.jsonc"))
    d.update(
        {
            "name": name,
            "iters": 2,
            "workers": 1,
            "out": str(tmp_path / "traj" / "weights.json"),
            "traj": str(tmp_path / "traj"),
            "data_dir": str(tmp_path / "traj" / "bc-data"),
            "backup_dir": str(tmp_path / "archive"),
            "backup_prefix": name,
            "eval": {"every_epochs": 0, "games_per_stage": 0, "levels": []},
        }
    )
    cur = tmp_path / "curricula"
    cur.mkdir(parents=True, exist_ok=True)
    (cur / f"{name}.bc.jsonc").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(bc_config, "CURRICULA_DIR", cur)
    return name


@pytest.fixture
def world(bc_course: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """一个进程里「一门 BC 课 + 一门 RL 课」的世界：真 serve 接线、零真运算。"""
    log: list[str] = []
    published: list[int] = []
    collected: list[int] = []
    landed: list[tuple[int, str]] = []
    archived: list[int] = []
    adopted: list[str] = []
    locks: list[str] = []
    clear_halt: list[str] = []
    hub = FakeHub()
    n = {"jid": 0}

    # ---- RL 侧：假引擎 + 假 args（`open_course` 的 RL 分支不进真解析链）----
    monkeypatch.setattr(loop_core, "TrainingLoop", FakeRLLoop)
    monkeypatch.setattr(loop_serve, "get_backend", lambda mode: f"backend:{mode}")
    monkeypatch.setattr(
        loop_plan, "load_ledger", lambda *a, **k: SimpleNamespace(next_it=1, rows=[])
    )
    monkeypatch.setattr(
        train_ledger, "load_ledger", lambda *a, **k: SimpleNamespace(next_it=1, rows=[])
    )

    # ---- BC 侧：真引擎，全部重活换假件 ----
    def collect_corpus(course: Any, course_fp: str, corpus_fp: str, it: int, *a: Any, **kw: Any):
        collected.append(int(it))

    def publish_bc_job(**kw: Any) -> dict:
        it = int(kw["it"])
        n["jid"] += 1
        jid = f"jid{n['jid']}"
        published.append(it)
        job_root = Path(kw["job_root"])
        (job_root / jid).mkdir(parents=True, exist_ok=True)
        manifest = {"job_id": jid, "it": it, "kind": "bc", "mode": "bc"}
        (job_root / jid / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def verify_and_land_bc(result: dict, manifest: dict, **kw: Any) -> None:
        landed.append((int(kw["it"]), str(manifest["job_id"])))

    def archive_round(course: Any, key: str, it: int, out: str, metrics: dict, *, smoke: bool):
        archived.append(int(it))

    real_find_round_job = bc_loop.find_round_job

    def find_round_job(job_root: Any, it: int, **kw: Any) -> Any:
        got = real_find_round_job(job_root, it, **kw)
        if got is not None:
            adopted.append(str(got[0]))
        return got

    monkeypatch.setattr(bc_loop, "collect_corpus", collect_corpus)
    monkeypatch.setattr(bc_loop, "publish_bc_job", publish_bc_job)
    monkeypatch.setattr(bc_loop, "verify_and_land_bc", verify_and_land_bc)
    monkeypatch.setattr(bc_loop, "archive_round", archive_round)
    monkeypatch.setattr(bc_loop, "find_round_job", find_round_job)
    monkeypatch.setattr(hub_client, "_request", hub.request)
    monkeypatch.setattr(hub_client, "set_cloud_halt", lambda *a, **kw: True)

    def fake_clear_halt(url: str, token: str, **kw: Any) -> bool:
        clear_halt.append(url)
        return True

    monkeypatch.setattr(hub_client, "clear_halt_on_startup", fake_clear_halt)

    # 单实例锁：真实现会在 nn-training/ 下落锁文件（测试不得写仓内文件）
    def fake_acquire(path: str, **kw: Any) -> bool:
        locks.append(str(path))
        return True

    monkeypatch.setattr("run_rl._acquire_run_rl_lock", fake_acquire)
    monkeypatch.setattr("run_rl._cleanup_run_rl_lock", lambda path: None)

    # ---- open_course：BC 课走**真**分支，RL 课给假 args ----
    real_open = loop_serve.open_course

    def open_course(course: str, *, argv: list[str] | None = None, traj_root: str = "tmp") -> Any:
        if course == bc_course:
            return real_open(
                course, argv=BC_ARGV if argv is None else argv, traj_root=str(tmp_path)
            )
        args = SimpleNamespace(mode="per-tick", traj=str(tmp_path / course), iters=2, out_log="")
        return CourseRuntime(course=course, args=args)

    monkeypatch.setattr(loop_serve, "open_course", open_course)
    return SimpleNamespace(
        bc=bc_course,
        tmp=tmp_path,
        hub=hub,
        log=log,
        published=published,
        collected=collected,
        landed=landed,
        archived=archived,
        adopted=adopted,
        locks=locks,
        clear_halt=clear_halt,
    )


# --------------------------------------------------------------- 单进程并行


def test_bc_course_runs_alongside_rl_and_yields_while_waiting(world: SimpleNamespace) -> None:
    """★ 一个进程带 BC + RL：BC 等云端回传时 **RL 课的轮照常推进**，两边都跑完。

    「BC 在等」这一段的墙钟不再白烧——这是把 BC 收进单进程 supervisor 的全部意义。
    """
    clock = FakeClock()
    rep = serve(
        [world.bc, "rl-a"],
        prepare=False,
        bun="bun",
        iters=2,
        step_mode=False,
        traj_root=str(world.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_steps=200,
        max_seconds=600.0,
    )

    assert rep.stop_reason == "all_settled"
    # BC 课：两轮都完成、指针指向 it3（= iters+1）、账本恰好两条完成事件
    assert rep.courses[world.bc]["rounds_done"] == 2
    assert rep.courses[world.bc]["state"] == "done"
    assert rep.courses[world.bc]["next_it"] == 3
    done = [
        e
        for e in read_events(Path(world.tmp) / "traj" / "training_log.jsonl")
        if e.get("event") == ROUND_DONE_EVENT
    ]
    assert [e["it"] for e in done] == [1, 2]
    # 每轮**只发布一次**（让位后再来问不重发）
    assert world.published == [1, 2] and world.landed == [(1, "jid1"), (2, "jid2")]
    # RL 课在同一段墙钟里跑完了它的两轮 ⇒ 真的发生过「BC 让位、RL 前进」
    assert [it for _c, it in FakeRLLoop.order] == [1, 2]
    assert clock.sleeps >= 2
    # 两门课共用一个进程、一个 supervisor：两门课的引擎都建过，退出时全部释放
    assert rep.engines["builds"] >= 2 and rep.engines["loaded"] == []
    assert set(rep.courses) == {world.bc, "rl-a"}


def test_bc_course_uses_round_granularity_even_in_step_mode(world: SimpleNamespace) -> None:
    """13 步表是 RL 的一轮；BC 课即使 serve 开了细粒度也必须是「一轮 = 一个任务」。"""
    from rl.loop_plan import round_tasks_for

    assert [t.kind for t in round_tasks_for(world.bc, 1)] == ["round"]
    assert len(round_tasks_for("rl-a", 1)) == len(ROUND_TASKS) == 13

    clock = FakeClock()
    rep = serve(
        [world.bc],
        prepare=False,
        bun="bun",
        step_mode=True,  # 默认细粒度
        traj_root=str(world.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_steps=200,
    )

    assert rep.stop_reason == "all_settled"
    assert rep.courses[world.bc]["rounds_done"] == 2  # 没被 13 步表卡住/ABORT
    assert world.published == [1, 2]


def test_bc_round_is_not_republished_when_the_engine_is_evicted(world: SimpleNamespace) -> None:
    """★ 引擎被池驱逐 = 等同一次重启：新引擎必须**从盘上认领**已发布的 job，不得重发。

    重发 = 新 jid = `bc-resume` 失效 = 从头训（一轮 GPU 时间白烧）；容量 1 时 BC 与 RL
    必然互相驱逐，所以这条路径在真机上是常态而不是边角。
    """
    clock = FakeClock()
    rep = serve(
        [world.bc, "rl-a"],
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        cache_courses=1,
        cache_mb=10**9,
        traj_root=str(world.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_steps=200,
    )

    assert rep.stop_reason == "all_settled"
    assert rep.engines["evictions"] >= 1  # 确实发生过驱逐（否则这条用例什么都没验）
    assert world.published == [1, 2]  # 每轮一次——**没有**因为重建而重发
    assert world.adopted  # 重建后的引擎认领了盘上那份 job


def test_bc_course_is_opened_with_the_run_bc_lock_and_its_own_traj(world: SimpleNamespace) -> None:
    """开课：① 锁用 `run_bc`（与 `run_bc.py --course X` 互相看得见）；② traj 来自课程配置。"""
    clock = FakeClock()
    serve(
        [world.bc],
        prepare=False,
        bun="bun",
        traj_root=str(world.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_steps=4,
    )

    assert len(world.locks) == 1
    # `.run_bc.<course>.lock`（与 `run_bc.py` 同名同路径 ⇒ 两条启动路径互相看得见）
    assert Path(world.locks[0]).name == f".run_bc.{world.bc}.lock"
    assert world.clear_halt == ["http://hub"]  # hub 传输 ⇒ 启动即清停机态


def test_a_broken_bc_course_does_not_take_down_the_rl_course(world: SimpleNamespace) -> None:
    """故障域 = 单课：BC 课配置坏了，RL 课照跑（与既有单课隔离同规，不新增语义）。"""
    clock = FakeClock()
    rep = serve(
        [world.bc, "rl-a"],
        prepare=False,
        bun="bun",
        iters=1,
        step_mode=False,
        argv=["--remote-transport", "pull"],  # 不给 hub → BC 课解析失败（pull 需要 hub+token）
        traj_root=str(world.tmp),
        now=clock.now,
        sleep=clock.sleep,
        poll_sec=1.0,
        max_steps=50,
    )

    assert rep.stop_reason == "all_settled"
    assert world.bc in rep.skipped and "pull" in rep.skipped[world.bc]
    assert world.bc not in rep.courses
    assert rep.courses["rl-a"]["rounds_done"] == 1
