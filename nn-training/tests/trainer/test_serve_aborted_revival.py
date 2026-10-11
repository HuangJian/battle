"""停腿（ABORTED）课的复活通道与**复活卫生**（2026-10-11 事故；plan/aborted-course-revival M1）。

事故形状：调度器级 5 连败 ⇒ 队列 `ABORTED`，而三条复活通道（`reopened_parked` /
`reopenable_skipped` / `fresh`）在设计上都够不到它 ⇒ 不重启 serve 就是终身停车。
本文件钉住：

1. **W1/W2**：停腿课进 `done_hooked`（不写收官副作用）+ 记 `SKIP_KIND_STEP` 跳过判据；
2. **W5**：判据（课程文件 / 开课标记 / 账本）变了 ⇒ 复活；没变 ⇒ 不复活、不刷日志；
3. **W3 复活卫生**：复活后**连续两轮仍活**——旧 `_leg_abort` 不清 = 立再死 / 一轮假收官
   （假引擎 `_FlakyLoop` 按真实 `step_ppo` 的形状建模「陈旧死腿判决 ⇒ 本轮立刻失败」）；
4. **W4 去重**：同一「停→开」只复活一次（`_revive_aborted` 不被二次触发）。

全部确定性：假时钟（不真睡）、注入 `open_course`/账本、tmp 目录夹具；不碰 torch/网络。
I-1（故障域）由既有 `tests/worker/test_loop_scheduler.py:262` 覆盖（D-7），此处不重复造。
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


class _FlakyLoop:
    """最小假引擎（轮粒度）：前 `fails_left` 次 `run_one_round` 抛普通异常（= 调度器级
    5 连败停腿的形状），并在失败时置**引擎级** `_leg_abort`（`loop_remote_fail.py:111` 的
    形状）——复活卫生（W3）的断言对象。

    ★ 陈旧死腿判决的形状按真实 `step_ppo` 建模：`_leg_abort` 还在 ⇒ 本轮**立刻失败**
    （`loop_round_steps.py:398-401` 的 finish(ROUND_STOP) / raise 路径）。没有这条，
    假引擎会「不管死腿照跑」，W3 的用例就抓不住「不清 `_leg_abort` ⇒ 复活立再死」。
    """

    instances: list[_FlakyLoop] = []
    fails_left = 0
    #: 成功跑满多少轮后自中断（测试用它收尾——成功轮不打瞌睡，钩子拦不住）。
    succeed_cap = 3

    def __init__(self, args: Any, backend: Any, bun: str, update_kwargs: Any) -> None:
        self.args = args
        self.setups = 0
        self.finishes: list[int] = []
        self.rounds: list[int] = []
        self._leg_abort = False
        self._remote_fail = 0
        self._consec_fail = 0
        _FlakyLoop.instances.append(self)

    def _setup(self) -> None:
        self.setups += 1

    def release_torch(self) -> None:
        pass

    def finish_course(self, it: int, *, drain: bool = True, block: bool = True) -> None:
        self.finishes.append(int(it))

    def run_one_round(self, it: int) -> Any:
        if not str(getattr(self.args, "traj", "")).endswith("bad"):
            return loop_core.RoundOutcome(loop_core.ROUND_NEXT, int(it))
        if _FlakyLoop.fails_left > 0:
            # 原始 5 连败（调度器级停腿的成因）：每次失败都把死腿判决留在引擎上。
            _FlakyLoop.fails_left -= 1
            self._leg_abort = True
            self._remote_fail = 3
            self._consec_fail += 1
            raise RuntimeError("boom: 远端不可用（调度器级 5 连败形状）")
        if self._leg_abort:
            # 5 连败已判死腿，而判决**没被清**（未过复活卫生）——与真实 step_ppo 同形：
            # 复活后的每一轮都立刻失败（立再死）。
            self._consec_fail += 1
            raise RuntimeError("leg ABORTED（陈旧判决，未过复活卫生）")
        self.rounds.append(int(it))
        if len(self.rounds) >= _FlakyLoop.succeed_cap:
            raise KeyboardInterrupt
        return loop_core.RoundOutcome(loop_core.ROUND_NEXT, int(it))


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """控制面/引擎/账本全部替身；控制文件重定向进 tmp（不碰操作员的活状态）。"""
    monkeypatch.setenv("NN_LOOP_CONTROL", str(tmp_path / "loop-control.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL_APPLIED", str(tmp_path / "loop-control.applied.json"))
    monkeypatch.setattr(loop_core, "TrainingLoop", _FlakyLoop)
    monkeypatch.setattr(loop_plan, "load_ledger", lambda *a, **k: _Ledger())
    monkeypatch.setattr(train_ledger, "load_ledger", lambda *a, **k: _Ledger())
    _FlakyLoop.instances.clear()
    _FlakyLoop.fails_left = 0
    _FlakyLoop.succeed_cap = 3
    loop_serve.close_course_sinks()
    yield
    _FlakyLoop.instances.clear()
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
            # 复活后要能连续跑几轮（预算别提前耗尽；`iters` 由 planner 的判据读）。
            iters=100,
            out_log="",
            remote_hub_url="",
            remote_token="",
            force=False,
        ),
    )


def _fake_open(root: Path) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def _open(course: str, **kw: Any) -> CourseRuntime:
        calls.append(course)
        return _runtime(root, course)

    return _open, calls


def _serve_until_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    courses: list[str],
    hook: Any,
) -> Any:
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
        step_mode=False,
    )


def _curricula(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "bad.jsonc").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", curricula)
    return curricula


def _post_abort_idle_hook(*, on_first_idle: Any = None, stop_after_idle: int = 6) -> Any:
    """停腿后的空转拍钩子：`fails_left` 归零 = 5 连败已把课停腿。

    第一个空转拍可执行 `on_first_idle`（复活触发）；其后 `stop_after_idle` 拍内自中断
    （给「没触发/复活失败」的场景收尾——复活成功后成功轮不打瞌睡，钩子不再被调到，
    由假引擎的 `succeed_cap` 兜底中断）。
    """
    state = {"idle": 0}

    def hook(_n: int) -> None:
        if _FlakyLoop.fails_left > 0:
            return
        state["idle"] += 1
        if state["idle"] == 1 and on_first_idle is not None:
            on_first_idle()
        if state["idle"] >= stop_after_idle:
            raise KeyboardInterrupt

    return hook


# ─────────────────── W1/W2：停腿的记账形状（无敌意触发） ───────────────────


def test_aborted_course_gets_no_course_finish_but_records_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1/W2：停腿**不写收官副作用**（不调 `finish_course`），但要记跳过判据（kind=step）。"""
    _curricula(tmp_path, monkeypatch)
    open_course, _calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "bad")
    _FlakyLoop.fails_left = 5

    rep = _serve_until_interrupt(
        tmp_path, monkeypatch, courses=["bad"], hook=_post_abort_idle_hook()
    )

    assert rep.courses["bad"]["state"] == "aborted"
    # W2：停腿被记成一次「可复活的跳过」（判据 = 课程文件/开课标记/账本）。
    assert sorted(rep.skipped) == ["bad"] and sorted(rep.skipped_at) == ["bad"]
    assert rep.skipped_at["bad"]["kind"] == loop_serve.SKIP_KIND_STEP
    # W1/I-3：停腿课不得写假收官（`finish_course` 一次都没被调）。
    assert _FlakyLoop.instances[-1].finishes == []
    assert rep.courses["bad"]["rounds_done"] == 0


# ─────────────────── W1：停课再开课 ⇒ reopened_parked 复活 ───────────────────


def test_aborted_course_revives_on_marker_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1：停腿课后「停课再开课」（标记 mtime 变新）⇒ 自动复活且**连续两轮仍活**。

    W3 的判别式：没有复活卫生时 `_leg_abort` 不清 ⇒ 复活后每一轮都立刻失败（立再死），
    `rounds_done` 永远到不了 2。
    """
    _curricula(tmp_path, monkeypatch)
    open_course, _calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    marker = _enable(tmp_path, "bad")
    _FlakyLoop.fails_left = 5

    def bump() -> None:
        st = marker.stat()
        os.utime(marker, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))

    rep = _serve_until_interrupt(
        tmp_path,
        monkeypatch,
        courses=["bad"],
        hook=_post_abort_idle_hook(on_first_idle=bump),
    )

    assert rep.courses["bad"]["state"] in ("ready", "running")
    assert rep.courses["bad"]["rounds_done"] >= 2  # ★ W3：复活后真的续跑（不是立再死）
    assert _FlakyLoop.instances[-1]._leg_abort is False  # ★ W3：判决态已清零
    assert _FlakyLoop.instances[-1].finishes == []  # 不是「一轮假收官」
    assert not rep.skipped and not rep.skipped_at  # 复活成功即摘名


# ─────────────────── W2/W3/W5：改课程文件 ⇒ reopenable_skipped 复活 ───────────────────


def test_aborted_course_revives_via_skipped_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W2：停腿课「改一次课程文件」（`SKIP_KIND_STEP` 判据变）⇒ 走 `_revive_aborted`
    复用原 runtime/引擎续跑（不重走 `open_course`、不重建引擎——C-0 前科）。"""
    curricula = _curricula(tmp_path, monkeypatch)
    open_course, calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "bad")
    _FlakyLoop.fails_left = 5

    def fix() -> None:
        (curricula / "bad.jsonc").write_text('{"fixed": true}\n', encoding="utf-8")

    rep = _serve_until_interrupt(
        tmp_path,
        monkeypatch,
        courses=["bad"],
        hook=_post_abort_idle_hook(on_first_idle=fix),
    )

    assert rep.courses["bad"]["state"] in ("ready", "running")
    assert rep.courses["bad"]["rounds_done"] >= 2  # ★ 复活后连续两轮仍活（W3）
    assert calls == ["bad"]  # 没重走 open_course（课已开、只是被停腿）
    assert len(_FlakyLoop.instances) == 1  # 没重建引擎（热复用）
    assert _FlakyLoop.instances[-1]._leg_abort is False


# ─────────────────── W4：同拍只复活一次（不双入队） ───────────────────


def test_stop_start_revives_once_not_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W4：同一「停→开」同时命中两条通道的判据 ⇒ 必须只复活一次。

    `reopened_parked` 复活成功后从 `skipped_at` 摘名 ⇒ 紧接着的 `reopenable_skipped`
    不再命中（否则会对同一 trigger 再 `_enqueue` 一次 = 双入队/双日志）。
    """
    _curricula(tmp_path, monkeypatch)
    open_course, _calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    marker = _enable(tmp_path, "bad")
    _FlakyLoop.fails_left = 5

    revive_calls: list[str] = []
    orig_revive = loop_serve._revive_aborted

    def spy(*args: Any, **kw: Any) -> Any:
        revive_calls.append(str(args[3]))
        return orig_revive(*args, **kw)

    monkeypatch.setattr(loop_serve, "_revive_aborted", spy)

    def bump() -> None:
        st = marker.stat()
        os.utime(marker, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))

    rep = _serve_until_interrupt(
        tmp_path,
        monkeypatch,
        courses=["bad"],
        hook=_post_abort_idle_hook(on_first_idle=bump),
    )

    assert rep.courses["bad"]["rounds_done"] >= 2  # 复活发生了（走 reopened_parked）
    assert revive_calls == []  # ★ W4：第二条通道没有被二次触发


# ─────────────────── I-2：判据没变 ⇒ 不复活、不刷日志 ───────────────────


def test_aborted_course_not_revived_when_fingerprint_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-2 回归防线：盘上事实没变 ⇒ 不复活（停腿 `state` 保持 aborted、零轮推进）。"""
    _curricula(tmp_path, monkeypatch)
    open_course, _calls = _fake_open(tmp_path)
    monkeypatch.setattr(loop_serve, "open_course", open_course)
    _enable(tmp_path, "bad")
    _FlakyLoop.fails_left = 5

    rep = _serve_until_interrupt(
        tmp_path, monkeypatch, courses=["bad"], hook=_post_abort_idle_hook()
    )

    assert rep.courses["bad"]["state"] == "aborted"
    assert rep.courses["bad"]["rounds_done"] == 0
    assert _FlakyLoop.instances[-1].finishes == []


# ─────────────────── W5 / W3：纯函数面 ───────────────────


def test_step_fingerprint_tracks_ledger_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W5：`SKIP_KIND_STEP` 的判据含**账本 mtime**——「人作废/重发 job」改的就是它。"""
    monkeypatch.setattr(loop_serve, "CURRICULA_DIR", tmp_path / "curricula")
    (tmp_path / "curricula").mkdir()
    traj = tmp_path / "c-step"
    traj.mkdir()
    ledger = traj / "training_log.jsonl"
    ledger.write_text("", encoding="utf-8")
    rec = {
        "c-step": {
            "kind": loop_serve.SKIP_KIND_STEP,
            "fp": loop_serve._skip_fingerprint("c-step", loop_serve.SKIP_KIND_STEP, tmp_path),
        }
    }
    assert loop_serve.reopenable_skipped(rec, tmp_path) == []
    st = ledger.stat()
    os.utime(ledger, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))
    assert loop_serve.reopenable_skipped(rec, tmp_path) == ["c-step"]


def test_revive_hygiene_clears_leg_state_and_only_this_course_context() -> None:
    """W3 纯函数面：清 `_leg_abort`/`_remote_fail`/`_consec_fail`、只 pop 本课 ctx、空件不炸。

    **不动** `runner.finished`（真硬边界/预算的语义，另一个谓词）——写在这防误加。
    """
    engine = SimpleNamespace(_leg_abort=True, _remote_fail=3, _consec_fail=5)
    runner = SimpleNamespace(contexts={"bad": object(), "other": object()}, finished=True)
    rt: Any = SimpleNamespace(course="bad", engine=engine, runner=runner)

    loop_serve._revive_hygiene(rt)

    assert engine._leg_abort is False
    assert engine._remote_fail == 0
    assert engine._consec_fail == 0
    assert sorted(runner.contexts) == ["other"]  # 只 pop 本课 ctx；别的课不动
    assert runner.finished is True  # 有意不动
    # 空件（引擎还没建 / 无 runner / rt 缺失）不得炸。
    loop_serve._revive_hygiene(None)
    empty_rt: Any = SimpleNamespace(course="c", engine=None, runner=None)
    loop_serve._revive_hygiene(empty_rt)
    cold_rt: Any = SimpleNamespace(course="c", engine=object(), runner=None)
    loop_serve._revive_hygiene(cold_rt)
