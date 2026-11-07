"""跑课前重取锁闸（2026-11-07 用户口径：控制台停课会删本课锁）。

停课删锁后，共享 trainer 的恢复/重开走内存表（`sup.resume` / `_revive_aborted`）**不重取锁**
——没有这道闸，该课就无锁裸跑（冒烟腿 / BC 一次性腿可与它双开写同一批 traj）。本文件钉：
① 锁缺失 / 陈旧 ⇒ 跑课前重取，照常执行；② 本进程持锁 ⇒ 零写盘；③ 别的**活**进程持有 ⇒
该课下线（ABORT + `SKIP_KIND_LOCK` 记账，锁一个字不动），持有者死 ⇒ 判据变 ⇒ 可复活。

全部确定性：tmp 锁目录（monkeypatch `NN_DIR`）+ 注入引擎/任务体 + 假存活探测；
不碰 torch / 网络 / 真实锁目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.instance_lock as instance_lock
import common.pid_probe as pid_probe
import trainer.loop_serve as loop_serve
from trainer.loop_serve import CourseRuntime, ServeReport, build_executor
from worker.loop_tasks import ABORT, Task


class _Engine:
    """最小假引擎：只数 `_setup`（ensure_ready 的首用判据）。"""

    def __init__(self) -> None:
        self.setups = 0

    def _setup(self) -> None:
        self.setups += 1


class _Runner:
    """最小假任务体：记录执行过的 task_id。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def executor(self, task: Any, queue: Any) -> str:
        self.calls.append(task.task_id)
        return "ran"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(loop_serve, "NN_DIR", tmp_path)
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    loop_serve.close_course_sinks()
    yield
    loop_serve.close_course_sinks()


def _course(
    tmp_path: Path, course: str = "c-lock"
) -> tuple[Any, CourseRuntime, _Runner, ServeReport]:
    lock = tmp_path / f".run_rl.{course}.lock"
    runner = _Runner()
    rt = CourseRuntime(course=course, args=SimpleNamespace(), lock_path=str(lock))
    rt.runner = cast(Any, runner)
    engine = _Engine()
    pool = SimpleNamespace(get=lambda _c: engine)
    report = ServeReport()
    execute = build_executor(cast(Any, pool), {course: rt}, report, traj_root=tmp_path)
    return execute, rt, runner, report


def _task(course: str = "c-lock") -> Task:
    return Task(course=course, it=1, kind="rollout")


def test_missing_lock_is_reacquired_before_step(tmp_path: Path) -> None:
    """停课把锁删了 ⇒ 跑课前重取（写回本进程 pid），照常执行。"""
    execute, rt, runner, report = _course(tmp_path)
    assert not Path(rt.lock_path).exists()
    result = execute(_task(), None)
    assert result == "ran"
    assert runner.calls == ["c-lock:it1:rollout"]
    holder, _exe, _ts = instance_lock.read_lock(rt.lock_path)
    assert holder == os.getpid()
    assert report.skipped == {}


def test_own_lock_is_not_rewritten(tmp_path: Path) -> None:
    """锁本来就在本进程手里 ⇒ 零写盘（mtime 不变），照常执行。"""
    execute, rt, runner, _report = _course(tmp_path)
    Path(rt.lock_path).write_text(f"{os.getpid()}|python|0", encoding="utf-8")
    before = Path(rt.lock_path).stat().st_mtime_ns
    result = execute(_task(), None)
    assert result == "ran"
    assert runner.calls == ["c-lock:it1:rollout"]
    assert Path(rt.lock_path).stat().st_mtime_ns == before


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    """陈旧锁（持有者已死）⇒ 接管重取，照常执行。"""
    execute, rt, _runner, _report = _course(tmp_path)
    Path(rt.lock_path).write_text("999999999|python|0", encoding="utf-8")
    result = execute(_task(), None)
    assert result == "ran"
    holder, _exe, _ts = instance_lock.read_lock(rt.lock_path)
    assert holder == os.getpid()


def test_foreign_live_holder_aborts_then_revives_on_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别的活进程持有 ⇒ 该课下线（不执行、锁不动、按 LOCK 记账）；持有者死 ⇒ 可复活。"""
    execute, rt, runner, report = _course(tmp_path)
    foreign = os.getpid() + 1
    Path(rt.lock_path).write_text(f"{foreign}|python|0", encoding="utf-8")
    # 假存活探测：foreign 活着（instance_lock 与 _lock_signature 各取一份，两处都要钉）。
    alive = {foreign}
    monkeypatch.setattr(instance_lock, "pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(pid_probe, "pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(instance_lock, "proc_cmdline", lambda pid: None)

    result = execute(_task(), None)
    assert result.status == ABORT
    assert runner.calls == []  # 一步都没跑
    assert report.skipped_at[rt.course]["kind"] == loop_serve.SKIP_KIND_LOCK
    # fail-closed：别人的锁一个字不动
    assert Path(rt.lock_path).read_text(encoding="utf-8").startswith(f"{foreign}|")
    # 持有者还活着 ⇒ 判据没变 ⇒ 不复活
    assert loop_serve.reopenable_skipped(report.skipped_at, tmp_path) == []

    # 持有者死了 ⇒ 锁签名变 ⇒ 可复活（复活后跑课前由重取闸接手）
    alive.clear()
    assert loop_serve.reopenable_skipped(report.skipped_at, tmp_path) == [rt.course]
