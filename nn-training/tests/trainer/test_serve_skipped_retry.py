"""P0 回归：被跳过的课「判据变了就自动重试」（2026-10-05 事故，plan/course-startup-recover §3.4）。

事故形状：`x20-adv3-open-r2` 因课程文件自相矛盾被 trainer 整课跳过，人**改好文件**后日志
一行变化都没有（`fresh` 过滤把 `report.skipped` 当终身黑名单）——唯一出路是重启共享 trainer，
而那会打断所有并行课程。本文件钉住三条回归防线：

1. 判据变了（课程文件从缺到有 / 内容变 / 停→开标记更新 / 锁释放 / 账本更新）⇒ 重试开课；
2. 判据没变 ⇒ 仍跳过且**不重复调用** `open_course`（防刷日志——`:851` 注释的原意）；
3. 一步级失败的课（仍在 `runtimes`、队列 ABORTED）走**第二条通道**：重置队列、复用原
   runtime/引擎重新入队（重建 runtime = C-0 无限 RETRY 前科）。

全部确定性：假时钟（不真睡）、注入的 `open_course`/账本、tmp 目录夹具；不碰 torch/网络。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trainer.loop_core as loop_core
import trainer.loop_plan as loop_plan
import trainer.loop_serve as loop_serve
import worker.train_ledger as train_ledger
from common.protocol import COURSE_ENABLE_MARKER
from trainer.loop_serve import CourseRuntime, serve
from worker.train.loop_util import course_lock_path


class _Ledger:
    """账本视图最小替身（`next_it` + `rows`）。"""

    def __init__(self, next_it: int = 1) -> None:
        self.next_it = next_it
        self.rows: list[Any] = []


class _Clock:
    """受控时钟：`sleep` 计数并可挂钩子（不真睡、不依赖墙钟顺序）。"""

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


class _BoomLoop:
    """最小假引擎：第一次 `run_one_round` 抛 SystemExit（课程配置错），之后正常返回。"""

    instances: list[_BoomLoop] = []
    booms = 1

    def __init__(self, args: Any, backend: Any, bun: str, update_kwargs: Any) -> None:
        self.args = args
        self.setups = 0
        self.finished: list[int] = []
        _BoomLoop.instances.append(self)

    def _setup(self) -> None:
        self.setups += 1

    def release_torch(self) -> None:
        pass

    def finish_course(self, it: int, *, drain: bool = True, block: bool = True) -> None:
        self.finished.append(int(it))

    def run_one_round(self, it: int) -> Any:
        # 只让名字以 "bad" 结尾的课炸（双课对照用例要在跑的那门照常推进）。
        if str(getattr(self.args, "traj", "")).endswith("bad") and _BoomLoop.booms > 0:
            _BoomLoop.booms -= 1
            raise SystemExit("[run_rl] --run-iters<0 需要课程声明 iters")
        return loop_core.RoundOutcome(loop_core.ROUND_NEXT, int(it))


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """控制面/引擎/账本全部替身；控制文件重定向进 tmp（不碰操作员的活状态）。"""
    monkeypatch.setenv("NN_LOOP_CONTROL", str(tmp_path / "loop-control.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL_APPLIED", str(tmp_path / "loop-control.applied.json"))
    monkeypatch.setattr(loop_core, "TrainingLoop", _BoomLoop)
    monkeypatch.setattr(loop_plan, "load_ledger", lambda *a, **k: _Ledger())
    monkeypatch.setattr(train_ledger, "load_ledger", lambda *a, **k: _Ledger())
    _BoomLoop.instances.clear()
    _BoomLoop.booms = 1
    loop_serve.close_course_sinks()
    yield
    _BoomLoop.instances.clear()
    loop_serve.close_course_sinks()


def _enable(root: Path, course: str) -> Path:
    """代操作员按下「开课」：写账本 + 开课标记（发现模式的课程表判据）。"""
    d = root / course
    d.mkdir(parents=True, exist_ok=True)
    (d / "training_log.jsonl").write_text("", encoding="utf-8")
    marker = d / COURSE_ENABLE_MARKER
    marker.write_text("", encoding="utf-8")
    return marker


def _runtime(root: Path, course: str) -> CourseRuntime:
    return CourseRuntime(
        course=course,
        args=SimpleNamespace(
            mode="per-tick",
            traj=str(root / course),
            iters=2,
            out_log="",
            remote_hub_url="",
            remote_token="",
            force=False,
        ),
    )


def _fake_open(root: Path, *, fail_first: bool = False) -> tuple[Any, list[str]]:
    """假 `open_course`：`fail_first` 时第一次抛课程配置错，之后成功。返回 (函数, 调用记录)。"""
    calls: list[str] = []

    def _open(course: str, **kw: Any) -> CourseRuntime:
        calls.append(course)
        if fail_first and len(calls) == 1:
            raise SystemExit(
                f"[course] {course}: kickstart_init=0.1 但 kickstart_ref 未开 —— "
                "缰绳没开，初值无消费方"
            )
        return _runtime(root, course)

    return _open, calls


# ────────────────────────── 判据快照：纯函数（不起进程） ──────────────────────────


def _record(curricula_root: Path, course: str, kind: str, traj_root: Path) -> dict[str, Any]:
    """按 `_record_skip` 的同一取法造一条跳过记账（测试可读）。"""
    return {"kind": kind, "fp": loop_serve._skip_fingerprint(course, kind, traj_root)}


def test_reopenable_skipped_detects_course_file_appearing_and_changing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD ①+③：文件从缺到有 / 内容变 ⇒ 重试；没变 ⇒ 不重试。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)

    rec = {"c-cfg": _record(curricula, "c-cfg", loop_serve.SKIP_KIND_CONFIG, tmp_path)}
    assert loop_serve.reopenable_skipped(rec, tmp_path) == []  # 文件不在 ⇒ 身份 None，没变
    (curricula / "c-cfg.jsonc").write_text("{}\n", encoding="utf-8")  # 文件出现 = 变了
    assert loop_serve.reopenable_skipped(rec, tmp_path) == ["c-cfg"]

    rec2 = {"c-cfg": _record(curricula, "c-cfg", loop_serve.SKIP_KIND_CONFIG, tmp_path)}
    assert loop_serve.reopenable_skipped(rec2, tmp_path) == []
    f = curricula / "c-cfg.jsonc"
    f.write_text("{\"fixed\": true}\n", encoding="utf-8")  # 人改好了（size 变）
    assert loop_serve.reopenable_skipped(rec2, tmp_path) == ["c-cfg"]


def test_reopenable_skipped_detects_marker_stop_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.4：用户「停→开」（开课标记 mtime 变新）也是明确的「我想让它跑」信号。"""
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", tmp_path / "curricula")
    (tmp_path / "curricula").mkdir()
    marker = _enable(tmp_path, "c-mark")
    rec = {"c-mark": _record(tmp_path / "curricula", "c-mark", loop_serve.SKIP_KIND_CONFIG, tmp_path)}
    assert loop_serve.reopenable_skipped(rec, tmp_path) == []
    st = marker.stat()
    os.utime(marker, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))
    assert loop_serve.reopenable_skipped(rec, tmp_path) == ["c-mark"]


def test_reopenable_skipped_tracks_lock_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁被占类：持有者活着 ⇒ 不重试；锁释放 / 持有者死 ⇒ 重试（自愈类）。"""
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", tmp_path / "curricula")
    monkeypatch.setattr(loop_serve, "NN_DIR", tmp_path)
    (tmp_path / "curricula").mkdir()
    lock = Path(course_lock_path(str(tmp_path), "c-lock", "run_rl"))
    lock.write_text(f"{os.getpid()}|python|0", encoding="utf-8")
    rec = {"c-lock": _record(tmp_path / "curricula", "c-lock", loop_serve.SKIP_KIND_LOCK, tmp_path)}
    assert loop_serve.reopenable_skipped(rec, tmp_path) == []  # 持有者还活着
    lock.write_text("0|python|0", encoding="utf-8")  # 持有者死了（pid 0 ⇒ 不可存活）
    assert loop_serve.reopenable_skipped(rec, tmp_path) == ["c-lock"]


def test_reopenable_skipped_tracks_ledger_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队失败类（修复对象是账本）：账本更新 ⇒ 重试；没动 ⇒ 不重试。"""
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", tmp_path / "curricula")
    (tmp_path / "curricula").mkdir()
    traj = tmp_path / "c-eq"
    traj.mkdir()
    ledger = traj / "training_log.jsonl"
    ledger.write_text("", encoding="utf-8")
    rec = {"c-eq": _record(tmp_path / "curricula", "c-eq", loop_serve.SKIP_KIND_ENQUEUE, tmp_path)}
    assert loop_serve.reopenable_skipped(rec, tmp_path) == []
    st = ledger.stat()
    os.utime(ledger, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))
    assert loop_serve.reopenable_skipped(rec, tmp_path) == ["c-eq"]


# ────────────────────────── serve 接线：开课期跳过 → 自动重试 ──────────────────────────


def _serve_until_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    courses: list[str],
    hook: Any,
    step_mode: bool = True,
):
    monkeypatch.setattr(loop_serve, "enabled_courses", lambda root: list(courses))
    clock = _Clock()
    clock.on_sleep = hook
    return serve(
        None,
        prepare=False,
        bun="bun",
        traj_root=str(tmp_path),
        now=clock.now,
        sleep=clock.sleep,
        step_mode=step_mode,
    )


def test_skipped_course_revives_when_course_file_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事故回归：跳过时课程文件不存在，人把它建好后 ⇒ 下一拍自动开课（不需重启）。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    open_course, calls = _fake_open(tmp_path, fail_first=True)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "r2")

    def hook(n: int) -> None:
        if n == 1:
            (curricula / "r2.jsonc").write_text("{}\n", encoding="utf-8")
        if n == 2:
            raise KeyboardInterrupt

    rep = _serve_until_interrupt(tmp_path, monkeypatch, courses=["r2"], hook=hook)

    assert rep.stop_reason == "interrupted"
    assert calls == ["r2", "r2"]  # 先跳过、判据变后重试一次
    assert "r2" not in rep.skipped and "r2" not in rep.skipped_at
    assert rep.courses["r2"]["state"] == "ready"


def test_skipped_course_not_retried_when_fingerprint_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """防刷日志（`:851` 注释的原意）：判据没变 ⇒ 多拍过去仍只调用一次 `open_course`。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "r2.jsonc").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    open_course, calls = _fake_open(tmp_path, fail_first=True)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "r2")

    def hook(n: int) -> None:
        if n == 3:  # 连续几个空转拍都不该重试
            raise KeyboardInterrupt

    rep = _serve_until_interrupt(tmp_path, monkeypatch, courses=["r2"], hook=hook)

    assert calls == ["r2"]
    assert "r2" in rep.skipped and "r2" in rep.skipped_at


def test_skipped_course_revives_on_stop_start_marker_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.4：用户在控制台「停→开」（标记 mtime 变新）⇒ 也复活的（不用等文件变）。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "r2.jsonc").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    open_course, calls = _fake_open(tmp_path, fail_first=True)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    marker = _enable(tmp_path, "r2")

    def hook(n: int) -> None:
        if n == 1:
            st = marker.stat()
            os.utime(marker, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))
        if n == 2:
            raise KeyboardInterrupt

    rep = _serve_until_interrupt(tmp_path, monkeypatch, courses=["r2"], hook=hook)

    assert calls == ["r2", "r2"]
    assert "r2" not in rep.skipped
    assert rep.courses["r2"]["state"] == "ready"


def test_running_sibling_unaffected_while_skipped_course_revives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 DoD（旧判据「行数/末行 it」不可判，F8）：双课 serve——一门在跑、一门被下线。

    事故的**代价面**就在这里：修一门课的配置以前要重启共享 trainer，会把所有并行课程
    一起打断。本用例钉住：在跑那门的进度推进且队列不被中止；被下线那门走第二条通道复活。
    """
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "bad.jsonc").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    open_course, calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "good")
    _enable(tmp_path, "bad")

    def hook(n: int) -> None:
        if n == 1:
            (curricula / "bad.jsonc").write_text('{"fixed": true}\n', encoding="utf-8")
        if n == 2:
            raise KeyboardInterrupt

    rep = _serve_until_interrupt(
        tmp_path, monkeypatch, courses=["good", "bad"], hook=hook, step_mode=False
    )

    assert rep.stop_reason == "interrupted"
    # 在跑的课：进度推进、队列终端不是 aborted、没被跳过/复活通道动过
    assert rep.courses["good"]["rounds_done"] >= 1
    assert rep.courses["good"]["state"] in ("ready", "done")
    assert "good" not in rep.skipped and "good" not in rep.skipped_at
    assert "good" not in rep.failures
    # 被一步级失败下线的课：判据变后重置队列重新入队（**没**重走 open_course）
    assert calls == ["good", "bad"]
    assert rep.courses["bad"]["state"] == "ready"
    assert "bad" not in rep.skipped and "bad" not in rep.skipped_at
    assert "bad" not in rep.failures


def test_step_level_abort_revives_via_requeue_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1 第二条通道：一步级失败的课仍在 `runtimes` ⇒ 重置队列、复用原 runtime/引擎。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "bad.jsonc").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    open_course, calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "bad")

    def hook(n: int) -> None:
        if n == 1:
            (curricula / "bad.jsonc").write_text("{\"fixed\": true}\n", encoding="utf-8")
        if n == 2:
            raise KeyboardInterrupt

    rep = _serve_until_interrupt(tmp_path, monkeypatch, courses=["bad"], hook=hook, step_mode=False)

    assert rep.stop_reason == "interrupted"
    assert calls == ["bad"]  # 没重走 open_course（课已开、只是被下线）
    assert rep.courses["bad"]["state"] == "ready"  # 队列已重置（不是 aborted）
    assert "bad" not in rep.skipped and "bad" not in rep.failures
    assert len(_BoomLoop.instances) == 1 and rep.engines["builds"] == 1  # 引擎没重建
    assert _BoomLoop.booms == 0
