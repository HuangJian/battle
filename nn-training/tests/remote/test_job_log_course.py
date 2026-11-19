"""云端 GPU worker 的 job 行带上课程名（2026-11-13，用户口径）。

为什么（现场）：共享 hub 会把**多门课**的活派给**同一台**云端 GPU worker，而那边每行日志
只有 16 位 `job_id` —— 它是幂等键的哈希（`common/job_identity.py`），故意不含课名（身份
不能跟着展示名漂）⇒ 看云端日志分不出正在训练的是哪门课。

修法：job 的日志出口包一层 `[course=<course_name>] `（`common/logutil.py::course_tag`）——
`course_name` 是发布端写进 manifest 的可读短名；**缺席 = 空串 = 逐字节旧行**（旧发布端 /
无课程）。两条腿各一处：pull（`remote/job_round.round`）与 push（`remote/worker_server._execute_job`）。

⚠ 预取腿（同行并发下载**下一份**活，可能是别的课）必须用**未包装**的 log：给它套上本 job
的课程名就是谎报归属。

判据口径：断的是「前缀是谁」与「原行还在不在位」（`startswith` / `endswith` 属性），
不按措辞扫子串 —— 日志文本就是本改动的交付物（2026-11-01 分类 A 类）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import remote.job_round as job_round
import remote.worker_server as worker_server
from common.logutil import course_tag, tagged
from common.protocol import ROLE_ONLINE
from remote import push_dispatch

JID = "0123456789abcdef"
COURSE = "g2-classic35"
TAG = f"[course={COURSE}] "


# ────────────────────────── 前缀本身 ──────────────────────────


def test_course_tag_is_empty_without_course_name() -> None:
    """缺席 / 空白 = 空串（旧发布端的 job 行保持逐字节旧形状）。"""
    assert course_tag({}) == ""
    assert course_tag({"course_name": ""}) == ""
    assert course_tag({"course_name": "   "}) == ""
    assert course_tag(cast(Any, None)) == ""
    assert course_tag({"course_name": COURSE}) == TAG


def test_tagged_is_passthrough_when_tag_is_empty() -> None:
    """空 tag 连函数身份都不换（不添一层包装 ⇒ 旧路径零额外开销、零改字节）。"""
    sink: list[str] = []

    def _log(msg: str) -> None:
        sink.append(msg)

    assert tagged(_log, "") is _log

    tagged(_log, TAG)("job x: 一行")
    assert sink == [TAG + "job x: 一行"]


def _assert_all_tagged(msgs: list[str]) -> None:
    """每一行都带同一个课程前缀（漏一行 = 操作员在那一行上又回到「只有 jid」）。"""
    assert [m for m in msgs if not m.startswith(TAG)] == [], msgs


# ────────────────────────── pull 腿 ──────────────────────────


class _Uploader:
    def submit(self, _task: Any) -> None:
        pass


def _pull_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: dict, msgs: list[str]) -> None:
    monkeypatch.setattr(job_round, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(job_round, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(job_round, "_wire_start", lambda *a, **k: None)
    monkeypatch.setattr(job_round, "_wire_flush", lambda *a, **k: None)

    def _run_job(_base: str, _token: str, _job: dict, **kw: Any) -> dict:
        kw["log"](f"job {JID}: PPO 训练中｜epochs=1/2")  # 训练核的原样行（下游）
        return {}

    job_round.run_one_round(
        "http://hub.invalid",
        "tok",
        job,
        part_dir=tmp_path,
        code_cache_dir=None,
        pf_store=None,
        polls_since_accept=0,
        worker_id="w-1",
        device="cpu",
        torch_threads=0,
        echo=False,
        restart_argv=None,
        role=ROLE_ONLINE,
        prefetch_depth=1,
        uploader=cast(Any, _Uploader()),
        run_job_fn=_run_job,
        log=msgs.append,
    )


def test_pull_round_logs_carry_the_course_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claim 行与 run_job 下游的每一行都带课名（一个 job 的整段生命周期）。"""
    msgs: list[str] = []
    job = {
        "job_id": JID,
        "manifest": {"it": 3, "kind": "ppo", "course_name": COURSE},
        "status": "ok",
        "lease_token": "",
    }
    _pull_round(tmp_path, monkeypatch, job, msgs)

    _assert_all_tagged(msgs)
    # 前缀之后仍是那两行原话（tag 只加前缀，不改正文）
    assert msgs[0].split(TAG, 1)[1].startswith(f"job {JID} claimed")
    assert any(m.endswith("PPO 训练中｜epochs=1/2") for m in msgs), msgs


def test_pull_round_keeps_old_lines_without_course_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧发布端（manifest 无 `course_name`）⇒ 日志逐字节旧形状（不加空 tag）。"""
    msgs: list[str] = []
    job = {"job_id": JID, "manifest": {"it": 1, "kind": "ppo"}, "status": "ok", "lease_token": ""}
    _pull_round(tmp_path, monkeypatch, job, msgs)

    assert msgs[0].startswith(f"job {JID} claimed"), msgs
    assert not any(m.startswith("[course=") for m in msgs), msgs


# ────────────────────────── push 腿 ──────────────────────────


def test_push_execute_logs_carry_the_course_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push 腿（云机常驻 worker_server）的收尾行也带课名——它先于 run_job 的日志。"""
    msgs: list[str] = []
    monkeypatch.setattr(worker_server, "run_job", lambda *_a, **_k: {"job_id": JID})
    monkeypatch.setattr(worker_server, "notify_result_ready", lambda *_a, **_k: False)
    monkeypatch.setattr(worker_server, "worker_wire_start", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_server, "worker_wire_flush", lambda *_a, **_k: None)

    state = worker_server.WorkerServerState(tmp_path)
    worker_server._execute_job(
        state,
        {"job_id": JID, "course_name": COURSE},
        b"",
        None,
        None,
        {},
        tmp_path,
        "cpu",
        0,
        False,
        log=msgs.append,
    )
    _assert_all_tagged(msgs)
    assert any(m.endswith("done — result ready for pickup") for m in msgs), msgs


# ────────────────────────── hub 侧（派发 / 回传）──────────────────────────


class _FakeHub:
    """派发器要的那几个调度面（鸭子类型；`push_dispatch` 本来就只靠这些）。"""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def release(self, *_a: Any, **_k: Any) -> bool:
        return True

    def heartbeat(self, *_a: Any, **_k: Any) -> bool:
        return True

    def store_job_failure(self, jid: str, *_a: Any, **_k: Any) -> None:
        self.failed.append(jid)

    def _now(self) -> float:
        return 0.0


def _dispatcher(tmp_path: Path, msgs: list[str], *, online: str = "") -> push_dispatch.PushDispatcher:
    workers = push_dispatch.PushWorkers(
        config_path=tmp_path / "no-such-config.json", log=lambda _m: None
    )
    if online:
        workers.add({"id": online, "url": "http://worker.invalid"})
        workers.note_fields(online, online=True)
    return push_dispatch.PushDispatcher(
        cast(Any, _FakeHub()), workers, "tok", now_fn=lambda: 0.0, log=msgs.append
    )


def test_hub_dispatch_lines_carry_the_course_name(tmp_path: Path) -> None:
    """hub 派发腿（`push_dispatch._run_one` → `_requeue`）也带课名。

    课名取 hub 的**课程键**（派发器就是按它轮转、与 `/admin/queue` 同字），不是 manifest 的
    `course_name` —— 两个值故意给成不一样，免得「凑巧相等」把来源遮住。
    """
    msgs: list[str] = []
    d = _dispatcher(tmp_path, msgs)
    d._run_one(
        COURSE,
        JID,
        {"id": "w1", "url": "http://worker.invalid"},
        "lease-1",
        {"course_name": "another-name"},
        tmp_path / "no-such-job",
        {"mode": "exclusive"},
    )
    _assert_all_tagged(msgs)
    assert any(m.endswith("回落队首（payload 不在盘上）—— 改推其它 worker（避免 -）") for m in msgs), msgs


def test_hub_result_intake_lines_carry_the_course_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hub 回传面（`_await_result` 的取结果腿）也带课名——410 终局失败那行。"""
    msgs: list[str] = []
    d = _dispatcher(tmp_path, msgs, online="w1")
    monkeypatch.setattr(push_dispatch, "_http", lambda *_a, **_k: (410, b'{"error": "no bun"}'))

    d._await_result(
        COURSE,
        JID,
        "w1",
        "http://worker.invalid",
        "key",
        "lease-1",
        {"mode": "exclusive"},
        0.0,
    )
    _assert_all_tagged(msgs)
    assert any(m.endswith("判定终局失败：no bun") for m in msgs), msgs
